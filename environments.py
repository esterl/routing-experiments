import copy
import logging
import threading
from datetime import timedelta,datetime
from heapq import heappop
from time import sleep, time

from utils import ssh, run, isiterable, check_root


""" Status """

BOOTING = 'booting'
UP = 'up'
DOWN = 'down'
BOOT = 'boot'
HALT = 'halt'
EXEC = 'exec'
START = 'start'
STOP = 'stop'
MON = 'monitor'
NETWORK = 'network'
EXPERIMENT = 'experiment'


class Environment(object):
    def prepare(self):
        raise NotImplementedError
    
    def run_experiment(experiment):
        raise NotImplementedError


class MLCEnvironment(Environment):
    #TODO read from a config file, common for MLC and python
    admin_idx = 0
    protocol_idx = 1
    ip4_prefix = 10
    ip4_admin_prefix = 0
    bridge_prefix = "mbr"
    veth_prefix = "veth"
    mac_prefix = "a0:cd:ef"
    ula_prefix= ["fd01:0:0", "fd02:0:0", "fd03:0:0"]
    monitors = []
    
    def prepare(self):
        check_root()
        run('./mlc-init-host.sh > /dev/null', async=False)
    
    def prepare_topology(self, topology):
        for [i,n] in enumerate(topology.vs):
            n['MLC_id'] = 1000+i
            n['up'] = False
        topology['MLC_max'] = 1000+i
        for link in topology.es:
            if 'quality' not in link.attribute_names():
                link['quality'] = 3
    
    @staticmethod
    def _get_ip4(node, idx=False, prefix=False):
        if idx is False:
            idx = MLCEnvironment.admin_idx
        if prefix is False:
            prefix = MLCEnvironment.ip4_admin_prefix
        words = (
            MLCEnvironment.ip4_prefix,
            prefix+idx,
            node/100,
            node%100
        )
        ip = '%i.%i.%i.%i' % words
        return ip
    
    def get_bridge(self, idx=None):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        return '%s%i' % (MLCEnvironment.bridge_prefix, idx)
    
    def get_interface(self, node, idx=None):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        n = self.experiment.topology.vs[node]['MLC_id']
        return '%s%i_%i' % (MLCEnvironment.veth_prefix, n, idx)
    
    def get_mac(self, node, idx=None):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        n = self.experiment.topology.vs[node]['MLC_id']
        words = (MLCEnvironment.mac_prefix, n/100, n%100, idx)
        return '%s:%02d:%02d:%02d' % words
    
    def get_ip6(self,node, idx=None, prefix_id=1):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        mac = self.get_mac(node, idx)
        mac = mac.split(':')
        words = [MLCEnvironment.ula_prefix[prefix_id-1]] + mac + [idx]
        return '%s:%s%s:%s%s:%s%s::%i' % tuple(words)
    
    def run_host(self, cmd):
        return run(cmd, mlc=False)
    
    def run_node(self, node, command, background=False):
        node = self.experiment.topology.vs[node]
        node_id = node['MLC_id']
        #TODO only if is waking
        if node['up'] == BOOTING:
            run('lxc-wait -n mlc%i -s RUNNING' % node_id)
            node['up'] = UP
        ip = MLCEnvironment._get_ip4(node_id)
        from utils import SSHClient
        thread = SSHClient(ip)
        thread.start()
        thread.execute(command, background)
#        thread = ssh(ip, command)
        return thread
    
    def run_experiment(self,experiment):
        self.monitors = []
        self.experiment = experiment
        self.prepare_topology(experiment.topology)
        # Go action by action:
        self.experiment.actions.sort()
        start = now = datetime.now()
        for action in self.experiment.actions:
            if now < start+action.at:
                sleep((start+action.at-now).total_seconds())
            self._process_action(action, action.at)
            now = datetime.now()
        self.experiment.done()
    
    def _process_action(self, action, now):
        if action.kind == BOOT:
            logging.debug('Booting nodes %s', action.target)
            self._start_nodes(action.target)
        elif action.kind == HALT:
            logging.debug('Halting nodes %s', action.target)
            self._stop_nodes(action.target)
        elif action.kind == EXEC:
            logging.debug('Executing %s', action.get_command())
            self._execute(action)
        elif action.kind == MON:
            for monitor in action.monitors:
                logging.debug('Runing monitor %s', monitor.__class__)
                self.monitors.append(monitor)
                monitor.action = None
                monitor.start(self)
        elif action.kind == STOP:
            logging.debug('Stopping experiment')
            for monitor in self.monitors:
                logging.debug('Stoping monitor %s', monitor.__class__)
                monitor.stop()
            run('mlc_loop --max %i -s' % self.experiment.topology['MLC_max'], async=False)
        elif action.kind == START:
            end = self._get_end()
            self.experiment.start_time = time()
            self.change_links(now,end)
    
    def _get_end(self):
        return self.experiment.actions[-1].at if self.experiment.actions else None
    
    def _set_link(self, src, dst, tx_q=None, rx_q=None):
        t = self.experiment.topology
        # Update the topology:
        tx = t.get_eid(src,dst)
        rx = t.get_eid(dst,src)
        if tx_q is not None:
            t.es[tx]['quality'] = tx_q
        if rx_q is not None:
            t.es[rx]['quality'] = rx_q
        # If nodes are up, modify the link
        src_online = t.vs[src]['up'] in (BOOTING, UP)
        dst_online = t.vs[dst]['up'] in (BOOTING, UP)
        if src_online and dst_online:
            c = {
                'src': t.vs[src]['MLC_id'],
                'dst': t.vs[dst]['MLC_id'],
                'idx': self.protocol_idx,
                'tx_q': t.es[tx]['quality'],
                'rx_q': t.es[rx]['quality'],
            }
            run('mlc_link_set %(idx)s %(src)s %(idx)s %(dst)s %(tx_q)i %(rx_q)i' % c, async=False)
    
    def _start_nodes(self, nodes):
        #Boot nodes
        if not isiterable(nodes):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.vs[node]
            run('mlc_loop --min %i -b' % n['MLC_id'], async=False)
            n['up'] = BOOTING
        #Stablish links
        t = self.experiment.topology
        for link in t.es():
            self._set_link(link.source, link.target)
    
    def _stop_nodes(self, nodes):
        logging.debug('Stopping nodes')
        if not isiterable(nodes):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.vs[node]
            run('mlc_loop --min %i -s' % n['MLC_id'])
            run('lxc-wait -n mlc%i -s STOPPED' % n['MLC_id'])
            n['up'] = DOWN
    
    # TODO Fix required parameters
    def _execute(self, action):
        if not isiterable(action.target):
            action.target = [action.target]
        cmds = action.get_command()
        list_mon = []
        for target in action.target:
            node = self.experiment.topology.vs[target]
            # TODO check if we want something different
            if node['up'] == DOWN:
                break
            thread = self.run_node(target, cmds)
#            ip = MLCEnvironment._get_ip4(node['MLC_id'])
#            for cmd in cmds.split("\n"):
#                thread = ssh(ip, cmd)
#                thread.next()
#                thread.next()
            # Obtain action pid:
            #Update read:
            thread.get_stdout()
            thread.execute(action.get_pid_cmd())
            try: action.pid = int(thread.proc.stdout.readline())
            except ValueError:
                sleep(0.1)
            thread.get_stdout()
            thread.execute(action.get_pid_cmd())
            action.pid = int(thread.proc.stdout.readline())
            # Monitors need to be multiplied for every command
            for monitor in action.monitors:
                aux_mon = copy.copy(monitor)
                self.monitors.append(aux_mon)
                list_mon.append(aux_mon)
                aux_mon.action = action
                aux_mon.target = target
                aux_mon.start(self)
        action.monitors = list_mon
    
    # TODO check if we really need "now"
    def change_links(self, now, stop=None):
        def _change_links(link_changes, now, stop):
            for (wait, src, dst, weight) in link_changes:
                if stop and now > stop:
                    break
                sleep(wait.total_seconds())
                now = now + wait
                self._set_link(src, dst, tx_q=weight)
        if 'link_changes' in self.experiment.topology.attributes():
            link_changes = self.experiment.topology['link_changes']
            thread = threading.Thread(target=_change_links, args=(link_changes, now, stop))
            thread.daemon = True
            thread.start()
