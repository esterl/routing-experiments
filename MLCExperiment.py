from datetime import timedelta
from ConfigParser import ConfigParser, NoOptionError
from time import sleep
#from enum import Enum
import subprocess
import paramiko
import itertools
import copy
import networkx as nx
from heapq import heappush, heappop
from functools import total_ordering
from monitors import *

#Type = Enum('Type', 'Boot Halt Exec Start Stop')
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


def coroutine(func):
    """ decorator that makes a generator function automatically advance
    to its first yield """
    def wrapper(*args, **kwargs):
        gen = func(*args, **kwargs)
        pid = gen.next()
        gen.pid = pid
        return gen
    return wrapper


def run(cmd, async=True):
    cmd = 'cd /home/ester/PhD/mlc; . ./mlc-vars.sh; %s' % cmd
    print '\033[1m$ %s \033[0m' % cmd
    out = subprocess.PIPE
    err = subprocess.PIPE
    p = subprocess.Popen(cmd, executable='/bin/bash', shell=True, stdout=out, stderr=err)
    if not async:
        p.wait()
    return p


#@coroutine
def ssh(addr, cmd):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(addr)
    #channel = client.get_transport().open_session()
    print '\033[1mssh@%s$ %s \033[0m' % (addr, cmd)
    #channel.exec_command(cmd)
    cmd = 'echo $$; %s' % cmd
    stdin, stdout, stderr = client.exec_command(cmd)
    
    yield int(stdout.readline())
    # Block until finished
    #channel.recv_exit_status()
    stdout = stdout.readlines()#channel.makefile('rb', -1).read()
    stderr = stderr.readlines()#channel.makefile_stderr('rb', -1).read()
    yield stdout, stderr


BOOTING = 'booting'
UP = 'up'
DOWN = 'down'

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
            if not 'weight' in d:
                topology.edge[src][dst]['weight']=3
    
    @staticmethod
    def _get_ip4(node, idx=False, prefix=False):
        if idx is False:
            idx = MLCEnvironment.admin_idx
        if prefix is False:
            prefix = MLCEnvironment.ip4_admin_prefix
        ip = '%s.%s.%s.%s' % (MLCEnvironment.ip4_prefix, prefix + idx,
                                node / 100, node % 100)
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
        return '%s:%02d:%02d:%02d' % (MLCEnvironment.mac_prefix, n/100, n%100, idx)
    
    def get_ip6(self,node, idx=None, prefix_id=1):
        if idx is None:
            idx = MLCEnvironment.protocol_idx
        mac = self.get_mac(node, idx)
        mac = mac.split(':')
        return '%s:%s%s:%s%s:%s%s::%i' % (MLCEnvironment.ula_prefix[prefix_id-1], mac[0], mac[1],
                                            mac[2], mac[3], mac[4], mac[5], idx)
    
    def run_host(self, cmd):
        print '\033[1m$ %s \033[0m' % cmd
        return subprocess.Popen(cmd, executable='/bin/bash', shell=True, 
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE)
    
    def run_node(self, node, command):
        node = self.experiment.topology.node[node]
        node_id = node['MLC_id']
        #TODO only if is waking
        if node['up']==BOOTING:
            run('lxc-wait -n mlc%i -s RUNNING' % node_id)
            node['up'] = True
        ip = MLCEnvironment._get_ip4(node_id)
        thread = ssh(ip, command)
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
                now = now + action.at
            self._process_action(action)
    
    def _process_action(self, action):
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
            #run('mlc_loop --max %i -s' % self.experiment.topology.graph['MLC_max'])
    
    def _start_nodes(self, nodes):
        #Boot nodes
        if not isinstance(nodes, list):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.node[node]
            run('mlc_loop --min %i -b' % n['MLC_id'])
            n['up'] = BOOTING
        #Stablish links
        t = self.experiment.topology
        for (src,dst) in t.edges():
            print "EDGES src: %s dst: %s" % (t.node[src], t.node[dst])
            if (t.node[src]['up'] in (BOOTING, UP)) and (t.node[dst]['up'] in (BOOTING, UP)):
                c = {
                    'src': t.node[src]['MLC_id'],
                    'dst': t.node[dst]['MLC_id'],
                    'idx': self.protocol_idx,
                    'tx_q': t.edge[src][dst]['weight'],
                    'rx_q': t.edge[dst][src]['weight'],
                }
                run('mlc_link_set %(idx)s %(src)s %(idx)s %(dst)s %(tx_q)i %(rx_q)i' % c)
    
    def _stop_nodes(self, nodes):
        if not isinstance(nodes, list):
            nodes = [nodes]
        for node in nodes:
            n = self.experiment.topology.node[node]
            run('mlc_loop --min %i -s' % n['MLC_id'])
            n['up'] = DOWN
    
    # TODO Fix required parameters
    def _execute(self, action):
        if not isinstance(action.target, list):
            action.target = [action.target]
        cmds = action.get_command()
        for target in action.target:
            node = self.experiment.topology.node[target]
            # TODO check if we want something different
            if node['up'] == DOWN:
                break
            if node['up'] == BOOTING:
                run('lxc-wait -n mlc%i -s RUNNING' % node['MLC_id'])
                node['up'] == UP
            ip = MLCEnvironment._get_ip4(node['MLC_id'])
            for cmd in cmds.split("\n"):
                thread = ssh(ip, cmd)
                thread.next()
                thread.next()
            # Obtain action pid:
            thread = ssh(ip, action.get_pid_cmd())
            thread.next()
            stdout, __ = thread.next()
            action.pid = int(stdout[0])
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
        #print action.pid
        
        #Start monitors:


@total_ordering
class Action(object):
    commands = ConfigParser({'pid':'ps -o pid -C "%(Command)s"'})
    
    _counter = itertools.count()
    
    def __str__(self):
        node = "%s@" % self._command if self.kind == EXEC else ''
        result = "[%s]%s %s%s" % (self.at, self.kind, node, self.target)
        return result
    
    def __init__(self, kind, at, target, _command="", monitors=[]):
        self._id = next(Action._counter)
        self.kind = kind
        self.at = at
        self.target = target
        self._command = _command
        self.pid=None
        self.monitors=monitors
    
    def __eq__(self, other):
        return self._id == other._id
    
    def __lt__(self, other):
        return ((self.at, self._id) < (other.at, other._id))
    
    @staticmethod
    def load_config(filename):
        Action.commands.read(filename)
    
    def get_command(self):
        return Action.commands.get(self._command, 'command')
    
    def get(self, key):
        try:
            return Action.commands.get(self._command, key)
        except NoOptionError:
            return None
    
    def get_pid_cmd(self):
        return Action.commands.get(self._command, 'pid')


class Topology(nx.Graph):
    pass


class Experiment(object):
    
    def __str__(self):
        return '\n'.join([ str(a) for a in self.actions ])
    
    def __init__(self,topology=Topology()):
        self.topology = topology
        self.actions = []
        self._last = timedelta(0)
    
    #TODO better use None instead of false
    def boot(self, nodes=None, at=None, delay=timedelta(0), inter_delay=timedelta(0)):
        self.schedule(BOOT, nodes, at, delay, inter_delay)
    
    def halt(self, nodes=None, at=None, delay=timedelta(0), inter_delay=timedelta(0)):
        self.schedule(HALT, nodes, at, delay, inter_delay)
    
    def apply_dynamic_links(self, at=None, delay=timedelta(0), inter_delay=timedelta(0)):
        self.schedule(START, NETWORK, at, delay, inter_delay)
    
    def execute(self, command, nodes=None, at=None, delay=timedelta(0), 
                inter_delay=timedelta(0), monitors=[]):
        self.schedule(EXEC, nodes, at, delay, inter_delay, command, monitors)
    
    def stop(self, at=None, delay=timedelta(0), inter_delay=timedelta(0)):
        self.schedule(STOP, EXPERIMENT, at, delay, inter_delay)
    
    def monitor(self, monitors, at=None, delay=timedelta(0), 
                inter_delay=timedelta(0)):
        self.schedule(MON, EXPERIMENT, at, delay, inter_delay, monitors=monitors)
    
    def schedule(self, action, target, at, delay, inter_delay, command="", 
                    monitors=[]):
        if target is None:
            target = self.topology.nodes()
        if at is None:
            at = self._last
        if inter_delay != timedelta(0) and isinstance(target, list):
            for t in target:
                self.schedule(action, t, at, delay, inter_delay, command, 
                            monitors)
                at = None
                delay = inter_delay
                monitors = copy.deepcopy(monitors)
        else:
            if callable(delay):
                delay = delay()
            self._last = at + delay
            entry = Action(action, self._last, target, command, monitors=monitors)
            heappush(self.actions, entry)


Action.load_config('/home/ester/PhD/mlc/MLCExperiment/cmd.conf')
t = nx.complete_graph(3)
e = Experiment(topology=t)
e.boot(range(0,3))
#m = monitor('bmx6', NETWORK)
m = CPUMonitor()
#m2 = MemoryMonitor()
#m3 = NetworkMonitor('/tmp/proba',outgoing=False)
m4 = ConnectivityMonitor('/tmp/proba-pings', src=0, dsts=[1,2], vlan=2)
#e.monitor(monitors=[m3])
e.monitor(monitors=[m4])
e.execute('BMX6', delay=timedelta(seconds=1), monitors=[m])
#e.apply_dynamic_links()
#m2 = monitor(connectivity, from=N+1, to=A)
#m3 = monitor(connectivity, from=N+1, to=B)

#e.boot(N+1, wait = wait1)
#e.execute(bmx6, nodes=N+1, monitor=[m2,m3])

#e.add_link(N+1,A)
e.stop(delay=timedelta(seconds=5))
print e
env = MLCEnvironment()
env.prepare()
env.run_experiment(e)
