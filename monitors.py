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
        cmd = "tcpdump -i %s -w %s -s 0 %s" % ( self.get_interface(environment),
                                    self.filename, self.get_filter(environment))
        self.thread = environment.run_host(cmd)
    
    def stop(self, environment):
        #Kill tcpdump
        self.thread.terminate()
        print zself.thread.stdout.read()
        print self.thread.stderr.read()
    
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
        cmd = ("while true; do "
            "pmap -d %i|tail -1|awk '{print $2 " " $4 " " $6}';"
            "sleep 1; done;") % self.pid
        self.thread = environment.run_node(self.node, cmd)
        self.pid_while = self.thread.next()
    
    def stop(self, environment):
        kill = environment.run_node(self.node, "kill %i" % (self.pid_while))
        kill.next()
        kill.next()
        stdout, stderr = self.thread.next()
        print stdout
        print stderr
    
    def info(self):
        pass

class CPUMonitor(Monitor):
    def start(self, environment):
        cmd = "perf stat -e cycles,instructions --pid {} --log-fd 2".format(self.pid)
        self.thread = environment.run_node(self.node, cmd)
        self.thread.next()
    
    def stop(self, environment):
        kill = environment.run_node(self.node, "killall -s SIGINT perf_3.2")
        kill.next()
        kill.next()
        stdout,stderr = self.thread.next()
        print stdout
        print stderr


class ConnectivityMonitor(Monitor):
    def __init__(self, filename, src, dsts, interface=1, vlan=1):
        self.filename = filename
        self.src = src
        self.dsts = dsts
        self.iface = interface
        self.vlan = vlan
    
    def start(self, environment):
        # Start pings from 'src' to 'dsts'
        if not isinstance(self.dsts, list):
            dsts = [dsts]
        for n in self.dsts:
            cmd = 'ping6 -n -i 0.1 %s' % environment.get_ip6(n, self.iface, 
                                                            self.vlan)
            thread = environment.run_node(self.src, cmd)
            thread.next()
        # Monitor with tcpdump
        iface = environment.get_interface(self.src, self.iface)
        cmd = "tcpdump -i %s -w %s -s 0 icmp6" % (iface, self.filename)
        self.thread = environment.run_host(cmd)
    
    def stop(self, environment):
        #Kill tcpdump
        self.thread.terminate()
        print self.thread.stdout.read()
        print self.thread.stderr.read()
