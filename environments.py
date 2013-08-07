import subprocess
import select
import fcntl
import functools
import os
import copy
from datetime import timedelta
from time import sleep
from heapq import heappush, heappop

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


popen = functools.partial(subprocess.Popen, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def non_block_read(output):
    fd = output.fileno()
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
    try:
        result = output.read()
    except:
        result = b''
    fcntl.fcntl(fd, fcntl.F_SETFL, fl)
    return result or b''

class SSHClient(object):
    shell = "/bin/sh"
    ssh_path = "/usr/bin/ssh"
    
    started = False
    stoped = False
    returncode = None
    stdout = ""
    stderr = ""
    
    def __init__(self, host, user=None, shell=None, ssh=None): 
        if user is None:
            self.user_host = host
        else:
            self.user_host = "{user}@{host}".format(user=user, host=host)
        if shell is not None:
            self.shell = shell
        if ssh is not None:
            self.ssh_path = ssh
    
    def _start_ssh_process(self):
        no_host_keys = 'StrictHostKeyChecking=no'
        args = [self.ssh_path, '-o', no_host_keys, self.user_host, self.shell]
        self.proc = popen(args)
        
        poll_result = self.proc.poll()
        if poll_result is not None:
            self.returncode = poll_result
            return self.proc.stderr.readlines()
        
        self.started = True
        return None
    
    def _read(self, _file):
        output = []
        
        while True:
            _r, _w, _e = select.select([_file],[],[], 0)
            if len(_r) == 0:
                break
            
            data = non_block_read(_r[0])
            if data is None:
                break
            
            output.append(data)
        return b''.join(output).decode()
    
    def write(self, data):
        if data[-1] != '\n':
            data += '\n'
        data = bytes(data, 'ascii')
        num = self.proc.stdin.write(data)
        self.proc.stdin.flush()
        return num
    
    def get_stderr(self):
        self.stderr += self._read(self.proc.stderr)
        return self.stderr
    
    def get_stdout(self):
        self.stdout += self._read(self.proc.stdout)
        return self.stdout
    
    def start(self):
        if self.started:
            raise Exception("Already started")
        self._start_ssh_process()
    
    def stop(self):
        if self.stoped:
            raise Exception("Already stoped")
        self.proc.terminate()
    
    def execute_background(self, command):
        #Run in background:
        if command[-1] != '&':
            command = command + '&'
        n = self.write(command)
        #Read any output that may be waiting
        self.stdout += self._read(self.proc.stdout)
        #Save pid
        command = 'echo $!'
        self.write(command)
        try: self.pid = int(self.proc.stdout.readline())
        except ValueError: self.pid=None
        return
    
    def execute_foreground(self, command):
        #get bash pid:
        self.stdout += self._read(self.proc.stdout)
        self.write('echo $$')
        try:
            self.pid = int(self.proc.stdout.readline())
        except ValueError:
            pass #Should throw an exception or something
        #run command:
        self.write(command)
        return

    def execute(self, command, background=False):
        if background:
            self.execute_background(command)
        else:
            self.execute_foreground(command)


class Environment(object):
    def prepare(self):
        raise NotImplementedError
    
    def run_experiment(experiment):
        raise NotImplementedError


def run(cmd, mlc=True, async=True):
    if mlc:
        cmd = 'cd /home/ester/PhD/mlc; . ./mlc-vars.sh; %s' % cmd
    print('\033[1m$ %s \033[0m' % cmd)
    p = popen(cmd, executable='/bin/bash', shell=True)
    if not async:
        p.wait()
    return p


def ssh(addr, cmd):
    client = SSHClient(addr)
    client.start()
    #channel = client.get_transport().open_session()
    print('\033[1mssh@%s$ %s \033[0m' % (addr, cmd))
    client.execute(cmd)
    return client


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
        print('\033[1mssh@%s$ %s \033[0m' % (node_id, command))
        if node['up']==BOOTING:
            run('lxc-wait -n mlc%i -s RUNNING' % node_id)
            node['up'] = UP
        ip = MLCEnvironment._get_ip4(node_id)
        thread = SSHClient(ip)
        thread.start()
        thread.execute(command, background)
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
            t.edge[src][dst] = tx_q
        if rx_q is not None:
            t.edge[dst][src] = rx_q
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
        if not isinstance(nodes, list):
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
        import threading
        def _change_links(link_changes, now):
            for (at, src, dst, weight) in link_changes:
                if now < at:
                    sleep(at.total_seconds()-now.total_seconds())
                now = at
                self._set_link(src, dst, tx_q=weight)
        link_changes = self.experiment.topology.graph['link_changes']
        thread = threading.Thread(target=_change_links, args=(link_changes, now))
        thread.start()
