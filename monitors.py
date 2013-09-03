#TODO import Environment?
import numpy
import numpy.lib.recfunctions as rf
import os.path
from datetime import datetime, timedelta

class Monitor(object):
    basename = ''
    filename = '/tmp/test'
    target = None
    action = None
    
    def start(self, environment):
        raise NotImplementedError
    
    def stop(self):
        raise NotImplementedError
    
    def get_global_data(self):
        return None
    
    def get_target_data(self):
        return (None, None)
    
    def set_filename(self,path, experiment_base):
        name = experiment_base + '_' + self.basename
        self.filename = os.path.join(path, experiment_base, name)
    
    def add_tag(self, tag):
        self.basename += '_' + tag


class NetworkMonitor(Monitor):
    command = None
    def __init__(self, action=None, incoming=True, outgoing=True, basename='traffic'):
        self.basename = basename
        self.action = action
        self.incoming = incoming
        self.outgoing = outgoing
        self.intervals = []
    
    def start(self, environment):
        self.env = environment
        # if self.action:
        #    self.node = self.action.target
        context = {
            'iface': self.get_interface(),
            'file': self.filename,
            'filter': self.get_filter()
        }
        cmd = "tcpdump -i %(iface)s -w %(file)s -s 0 %(filter)s" % context
        self.thread = environment.run_host(cmd)
    
    def stop(self):
        # Kill tcpdump
        self.thread.terminate()
    
    def add_interval(self,start,end,tag):
        self.intervals.append((tag,(start,end)))
    
    def get_global_data(self):
        if self.target is 'experiment':
            result = dict()
            for (tag, interval) in self.intervals:
                result.update(self._get_data(interval, tag))
            result.update(self._get_data())
            return result
        else:
            return None
    
    def get_target_data(self):
        if self.target is 'experiment':
            return (None, None)
        else:
            result = dict()
            for (tag, interval) in self.intervals:
                result.update(self._get_data(interval, tag))
            result.update(self._get_data())
            return (self.node, result)
            # TODO fix it so that get_X_data does not repeat code
    
    def _get_data(self, interval=None, tag=None):
        headers = ["Filename", "Num_packets", "Bytes", "Byte_rate", "Packet_rate"]
        if tag is not None:
            headers = [h + "_" + tag for h in headers]
        if interval is None:
            capinfos = "capinfos -cdTmxyr %s" % self.filename
            p = self.env.run_host(capinfos)
            stats = p.stdout.readline().decode()
            stats = stats.strip().split(',')
        else:
            (start, end) = interval
            filters = []
            if isinstance(start, datetime) and isinstance(end,datetime):
                filters.append("frame.time_epoch > %f" % time.mktime(start.timetuple()))
            elif isinstance(start, timedelta):
                filters.append("frame.time_relative > %f" % start.total_seconds())
            if isinstance(end, datetime):
                filters.append("frame.time_epoch < %f" % time.mktime(end.timetuple()))
            elif isinstance(end, timedelta):
                filters.append("frame.time_relative > %f" % end.total_seconds())
            filters = "&&".join(filters)
            tshark = (
                'tshark -q -z io,stat,0,'
                '"MIN(frame.time_relative)frame.time_relative and %s",'
                '"MAX(frame.time_relative)frame.time_relative and %s",'
                '"COUNT(frame)frame and %s",'
                '"SUM(frame.len)frame.len and %s",'
                ' -r %s |tail -n2 |head -n1'
            ) % (filters, filters, filters, filters, self.filename)
            p = self.env.run_host(tshark)
            stats = p.stdout.readline().decode()
            stats = stats.split()
            duration = float(stats[2]) - float(stats[1])
            stats = [
                self.filename,
                stats[3],
                stats[4],
                float(stats[4])/duration,
                float(stats[3])/duration
            ]
        return dict(zip(headers, stats))
            
    def get_interface(self):
        # If target == EXPERIMENT
        if self.target == 'experiment':
            return self.env.get_bridge()
        else:
            return self.env.get_interface(self.target)
    
    def get_filter(self):
        filters = []
        target = self.target
        if target is not 'experiment':
            if not self.incoming and self.outgoing:
                filters.append('(ether dst %s)' % (environment.get_mac(target)))
            if not self.outgoing and self.incoming:
                filters.append('(ether src %s)' % (environment.get_mac(target)))
        if self.action:
            filter = self.action.get('network filter')
            if filter:
                filters.append( '(%s)' % filter)
        if len(filters) >= 1:
            return "'(%s)'" % (" and ".join(filters))
        else:
            return ''


class MemoryMonitor(Monitor):
    def __init__(self, basename='memory'):
        self.basename=basename
    
    def start(self, environment):
        self.env = environment
        # From pmap output get 'mapped' 'writeable/private' 'shared'
        pmap = "pmap -d %i|tail -1|awk '{print $2 \" \" $4 \" \" $6}'" % self.action.pid
        pmap = "while true; do %s; sleep 10; done > /tmp/%s" % ( pmap, self.basename)
        thread = environment.run_node(self.target, pmap, background=True)
        self.pid = thread.pid
        thread.stop()
    
    def stop(self):
        thread = self.env.run_node(self.target, 'kill %i' % self.pid)
        thread.execute('cat /tmp/%s' % self.basename)
        stdout = thread.get_stdout().split('\n')
        thread.stop()
        data = []
        for line in stdout:
            if line && len(line.split())==3:
                data.append(numpy.fromstring(line, dtype=int, sep='K'))
        data = numpy.vstack(data)
        # Compute average:
        mean = data.mean(axis=0).tolist()
        header = ['mapped','writeable/private','shared']
        self.data = dict(zip(header, mean))
        # TODO Update numpy version so that it can have a header or write manually the header
        # numpy.savetxt(self.filename, data, delimiter=',', header=','.join(header))
        numpy.savetxt(self.filename, data, delimiter=',')
    
    def get_target_data(self):
        return (self.target, self.data)

# TODO fix so that get_target_data is the one computing
class CPUMonitor(Monitor):
    def __init__(self, basename='cpu'):
        self.basename = basename
    
    def start(self, environment):
        self.env = environment
        perf = ('perf stat -x , -e task-clock,cycles,instructions '
                '--pid %i --log-fd 2 2> /tmp/%s'
                ) % (self.action.pid, self.basename)
        thread = environment.run_node(self.target, perf, background=True)
        self.pid = thread.pid
        thread.stop()
    
    def stop(self):
        thread = self.env.run_node(self.target, 'kill -s 2 %s' % self.pid)
        thread.execute('cat /tmp/perf')
        output = thread.get_stdout().split('\n')
        output = [ line.split(',') for line in output if line != '']
        self.data = dict()
        for line in output:
            self.data[line[1]] = line[0]
        thread.stop()
    
    def get_target_data(self):
        return (self.target, self.data)



class ConnectivityMonitor(Monitor):
    def __init__(self, src, dsts, interface=1, vlan=1, basename='connectivity'):
        self.basename = basename
        self.src = src
        self.dsts = dsts
        self.iface = interface
        self.vlan = vlan
        self.data = None
    
    def start(self, environment):
        self.env = environment
        # Start pings from 'src' to 'dsts'
        if not isinstance(self.dsts, list):
            self.dsts = [self.dsts]
        # TODO same ssh connection for all pings
        self.dst_ips = [environment.get_ip6(dst, self.iface, self.vlan) for dst in self.dsts]
        for ip in self.dst_ips:
            ping = 'ping6 -q -n -i 0.1 %s' % ip
            thread = environment.run_node(self.src, ping, background=True)
            thread.stop()
        # Monitor with tcpdump
        iface = environment.get_interface(self.src, self.iface)
        tcpdump = 'tcpdump -i %s -w %s -s 0 icmp6' % (iface, self.filename)
        self.thread = environment.run_host(tcpdump)
    
    def stop(self):
        # Kill tcpdump
        self.thread.terminate()
        # Kill pings:
        thread = self.env.run_node(self.src, 'killall ping6')
        thread.stop()
    
    def get_target_data(self):
        if self.data is None:
            self.data = []
            for i in range(len(self.dst_ips)):
                target = (self.src, self.dsts[i])
                data = self._get_data(self.dst_ips[i])
                self.data.append((target,data))
        return self.data
    
    def _get_data(self, ip):
        # Read summary
        tshark = ('tshark -r %s '
              '-q -z icmpv6,srt,'
              'ipv6.addr==%s'
              '|tail -n5|head -n1'
            ) % (self.filename, ip)
        p = self.env.run_host(tshark)
        summary = p.stdout.readline().decode().split()
        # Get longest window
        tshark = ('tshark -r %s '
                  '-T fields -e frame.time_relative -e icmpv6.echo.sequence_number '
                  '"ipv6.addr==%s&&icmpv6.type==%i" '
                  '-E separator=,'
                ) # %(filename, ip, icmpv6.type)
        p = self.env.run_host(tshark % (self.filename, ip, 128))
        reqs = p.stdout.readlines()
        data = [ numpy.fromstring(line.decode().strip(), dtype=float , sep=',') for line in reqs ]
        reqs = numpy.vstack(data) if data else numpy.array([[]])
        reqs.dtype = [('time_req',float),('id',float)]
        p = self.env.run_host(tshark % (self.filename, ip, 129))
        reps = p.stdout.readlines()
        data = [ numpy.fromstring(line.decode().strip(), dtype=float, sep=',') for line in reps ]
        reps = numpy.vstack(data) if data else numpy.array([[]])
        reps.dtype = [('time_rep',float),('id',float)]
        max_offline = '<NA>'
        if reqs.size > 0:
            res = rf.join_by('id', reps, reqs, jointype='outer')
            # Find largest "True"
            max_offline = 0
            current_offline = 0
            last_sent = 0
            i = 0
            while i < res.size:
                #Offline window:
                while i < res.size and res.mask['time_rep'][i]:
                    i += 1
                if i < res.size:
                    current_offline = res.data['time_rep'][i] - last_sent
                else:
                    current_offline = res.data['time_req'][i-1] - last_sent
                #Online window:
                while i < res.size and not res.mask['time_rep'][i]:
                    last_sent = res.data['time_req'][i]
                    i += 1
                if current_offline > max_offline:
                    max_offline = current_offline
                    current_offline += 1
        # Format data:
        data = dict()
        headers = [ 'Filename', 'Requests', 'Replies', 'Lost', 'Max_offline']
        values = [ self.filename, summary[0], summary[1], summary[2], max_offline]
        return dict(zip(headers,values))
