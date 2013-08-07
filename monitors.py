#TODO import Environment?

class Monitor(object):
    def start(self, environment):
        raise NotImplementedError
    
    def stop(self, environment):
        raise NotImplementedError
    
    def info(self):
        raise NotImplementedError


class NetworkMonitor(Monitor):
    def __init__(self, filename, node=None, incoming=True, outgoing=True):
        self.filename = filename
        self.node = node
        self.incoming = incoming
        self.outgoing = outgoing
        self.action = None
    
    def start(self, environment):
        if self.action:
            self.node = self.action.target
        context = {
            'iface': self.get_interface(environment),
            'file': self.filename,
            'filter': self.get_filter(environment)
        }
        cmd = "tcpdump -i %(iface)s -w %(file)s -s 0 %(filter)s" % context
        self.thread = environment.run_host(cmd)
    
    def stop(self, environment):
        #Kill tcpdump
        self.thread.terminate()
        print(self.thread.stdout.read())
        print(self.thread.stderr.read())
    
    def info(self):
        pass
    
    def get_interface(self, environment):
        if self.node is None:
            return environment.get_bridge()
        else:
            return environment.get_interface(self.node)
    
    def get_filter(self, environment):
        filters = []
        node = self.node
        if not node is None:
            if not self.incoming and self.outgoing:
                filters.append('(ether dst %s)' %(environment.get_mac(node)))
            if not self.outgoing and self.incoming:
                filters.append('(ether src %s)' %(environment.get_mac(node)))
        if self.action:
            filter = self.action.get('network filter')
            if filter:
                filters.append( '(%s)' % filter)
        if len(filters) >= 1:
            return '\'(%s)\'' % (" and ".join(filters))
        else: return ''


class MemoryMonitor(Monitor):
    def start(self, environment):
        pmap = "pmap -d %i|tail -1|awk '{print $2 " " $4 " " $6}'" % self.pid
        cmd = "while true; do %s; sleep 1; done;" % pmap 
        self.thread = environment.run_node(self.node, cmd, background=False)
    
    def stop(self, environment):
        k = environment.run_node(self.node, 'kill %i' % self.thread.pid)
        k.stop()
        print(self.thread.get_stderr())
        print(self.thread.get_stdout())
    
    def info(self):
        pass


class CPUMonitor(Monitor):
    def start(self, environment):
        cmd = "perf stat -e cycles,instructions --pid {} --log-fd 2".format(self.pid)
        self.thread = environment.run_node(self.node, cmd, background=True)
    
    def stop(self, environment):
        self.thread.execute("kill -s SIGINT %i" % self.thread.pid)
        print(self.thread.get_stderr())
        print(self.thread.get_stdout())
        self.thread.stop()


class ConnectivityMonitor(Monitor):
    def __init__(self, filename, src, dsts, interface=1, vlan=1):
        self.filename = filename
        self.src = src
        self.dsts = dsts
        self.iface = interface
        self.vlan = vlan
        self.ping_threads = []
    
    def start(self, environment):
        # Start pings from 'src' to 'dsts'
        if not isinstance(self.dsts, list):
            dsts = [dsts]
        # TODO same ssh connection for all pings
        for n in self.dsts:
            ipv6 = environment.get_ip6(n, self.iface, self.vlan)
            cmd = 'ping6 -n -i 0.1 %s' % ipv6
            thread = environment.run_node(self.src, cmd, background=True)
            self.ping_threads.append(thread)
        # Monitor with tcpdump
        iface = environment.get_interface(self.src, self.iface)
        cmd = "tcpdump -i %s -w %s -s 0 icmp6" % (iface, self.filename)
        self.thread = environment.run_host(cmd)
    
    def stop(self, environment):
        #Kill tcpdump
        self.thread.terminate()
        print(self.thread.stdout.read())
        print(self.thread.stderr.read())
        #Kill pings:
        for thread in self.ping_threads:
            thread.execute("kill %i" % thread.pid)
            thread.stop()
