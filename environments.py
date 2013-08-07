import copy
import threading
from datetime import timedelta
from heapq import heappop
from time import sleep

from utils import ssh, run, isiterable


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
        run('./mlc-init-host.sh > /dev/null', async=False)
    
    def prepare_topology(self, topology):
        for [i,n] in enumerate(topology.nodes()):
            topology.node[n]['MLC_id'] = 1000+i
            topology.node[n]['up'] = False
        topology.graph['MLC_max'] = 1000+i
        for (src,dst,d) in topology.edges(data=True):
            if not 'quality' in d:
                topology.edge[src][dst]['quality']=3
    
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
        n = self.experiment.topology.node[node]['MLC_id']
        return '%s%i_%i' % (MLCEnvironment.veth_prefix, n, idx)
    
    def get_mac(self, node, idx=None):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        n = self.experiment.topology.node[node]['MLC_id']
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
        node = self.experiment.topology.node[node]
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
        self.experiment = experiment
        self.prepare_topology(experiment.topology)
        now = timedelta(0)
        # Go action by action:
        while experiment.actions:
            action = heappop(experiment.actions)
            if now < action.at:
                sleep(action.at.total_seconds() - now.total_seconds())
                now = action.at
            self._process_action(action, now)
    
    def _process_action(self, action, now):
        if action.kind == BOOT:
            self._start_nodes(action.target)
        elif action.kind == HALT:
            self._stop_nodes(action.target)
        elif action.kind == EXEC:
            self._execute(action)
        elif action.kind == MON:
            for monitor in action.monitors:
                self.monitors.append(monitor)
                monitor.action = None
                monitor.start(self)
        elif action.kind == STOP:
            for monitor in self.monitors:
                monitor.stop(self)
        elif action.kind == START:
            self.change_links(now)
            #run('mlc_loop --max %i -s' % self.experiment.topology.graph['MLC_max'])
    
    def _set_link(self, src, dst, tx_q=None, rx_q=None):
        t = self.experiment.topology
        # Update the topology:
        if tx_q is not None:
            t.edge[src][dst]['quality'] = tx_q
        if rx_q is not None:
            t.edge[dst][src]['quality'] = rx_q
        # If nodes are up, modify the link
        src_online = t.node[src]['up'] in (BOOTING, UP)
        dst_online = t.node[dst]['up'] in (BOOTING, UP)
        if src_online and dst_online:
            c = {
                'src': t.node[src]['MLC_id'],
                'dst': t.node[dst]['MLC_id'],
                'idx': self.protocol_idx,
                'tx_q': t.edge[src][dst]['quality'],
                'rx_q': t.edge[dst][src]['quality'],
            }
            run('mlc_link_set %(idx)s %(src)s %(idx)s %(dst)s %(tx_q)i %(rx_q)i' % c)
    
    def _start_nodes(self, nodes):
        #Boot nodes
        if not isiterable(nodes):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.node[node]
            run('mlc_loop --min %i -b' % n['MLC_id'])
            n['up'] = BOOTING
        #Stablish links
        t = self.experiment.topology
        for (src,dst) in t.edges():
            self._set_link(src,dst)
    
    def _stop_nodes(self, nodes):
        if not isiterable(nodes):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.node[node]
            run('mlc_loop --min %i -s' % n['MLC_id'])
            n['up'] = DOWN
    
    # TODO Fix required parameters
    def _execute(self, action):
        if not isiterable(action.target):
            action.target = [action.target]
        cmds = action.get_command()
        for target in action.target:
            node = self.experiment.topology.node[target]
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
            action.pid = int(thread.proc.stdout.readline())
            for monitor in action.monitors:
                aux_mon = copy.deepcopy(monitor)
                self.monitors.append(aux_mon)
                aux_mon.action = action
                aux_mon.pid = action.pid
                aux_mon.node = target
                aux_mon.start(self)
        # TODO retry several times
        #for cmd in cmds.split("\n"):
        #    try: stdin,stdout,stderr = ssh.exec_command(action.get_command())
        #    except paramiko.SSHException: return
        #stdin, stdout, stderr = ssh.exec_command(action.get_pid_cmd())
        #while True:
        #    stdin, stdout, stderr = ssh.exec_command(action.get_pid_cmd())
        #    try: 
        #        action.pid = int(stdout.readline())
        #        break
        #    except ValueError:
        #        pass
        #print(action.pid)
        
        #Start monitors:
    
    def change_links(self, now):
        def _change_links(link_changes, now):
            for (at, src, dst, weight) in link_changes:
                if now < at:
                    sleep(at.total_seconds()-now.total_seconds())
                now = at
                self._set_link(src, dst, tx_q=weight)
        link_changes = self.experiment.topology.graph['link_changes']
        thread = threading.Thread(target=_change_links, args=(link_changes, now))
        thread.start()
