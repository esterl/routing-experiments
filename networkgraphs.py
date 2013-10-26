import igraph
import logging
import random
import xml.etree.ElementTree as ET
import urllib.request
from heapq import heappop, heappush
from datetime import timedelta as delta

# TODO:
# - Get max distance

class NetworkGraph(igraph.Graph):
    @classmethod
    def from_cnml(cls, area):
        """
        Imports a Guifi.net area into networkx format. Only working nodes and 
        working links are considered. Results in an undirected graph.
        
        Keyword argument:
        area -- string that refers to the CNML area
        """
        url = 'http://guifi.net/en/guifi/cnml/%s/detail' % area
        response = urllib.request.urlopen(url)
        tree = ET.parse(response)
        gr = NetworkGraph()
        nodes = list(tree.getroot().findall(".//node[@status='Working']"))
        # Add every node in the graph
        for node in nodes:
            gr.add_vertex(node.get('id'),lon=node.get('lon'), lat=node.get('lat'))
        # Add every link within the graph
        for node in nodes:
            interfaces = node.findall('.//interface')
            src = node.get('id')
            for interface in interfaces:
                if interface.get('ipv4').startswith('172'):
                    links = interface.findall(".//link[@link_status='Working']")
                    for link in links:
                        dst = link.get('linked_node_id')
                        # Only add the link once and if the destination is in 
                        # the graph
                        if dst in gr.vs['name'] and src < dst:
                            gr.add_edge(src, dst)
        # Return only the biggest component
        return gr.clusters().giant()
    
    def random_link_changes(self, time_wait, time_off, duration, simultaneous=True):
        class Link:
            def __init__(self, src, dst, quality):
                self.src = src
                self.dst = dst
                self.quality = quality
            
            def __init__(self, link):
                self.link = link
                self.src = link.source
                self.dst = link.target
                self.quality = link['quality']
            
            def delete(self):
                self.link.delete()
            
            def __str__(self):
                return '(%s,%s,%s)' % (self.src, self.dst, self.quality)
        
        # We only consider links that won't partition the graph
        time = time_wait() if callable(time_wait) else time_wait
        changes = []
        graph = self.copy()
        # Difference not working! links = list(set(graph.es).difference(graph.bridges()))
        bridges = [ link.index for link in graph.bridges()]
        links = list(set(graph.es.indices).difference(bridges))
        off_links = []
        while time < duration:
            # Randomly choose a link
            off = time_off()+time if callable(time_off) else time_off+time
            next = time_wait() if callable(time_wait) else time_wait
            if links:
                link = random.choice(links)
                link = Link(graph.es(link)[0])
                changes.append((time, link, 0))
                # And disable it
                heappush(off_links, (off,link))
                link.delete()
            time = next+time if simultaneous else next+off
            # Enable links that should be on again
            while off_links and off_links[0][0] < time:
                (on_time,link) = heappop(off_links)
                if on_time < duration:
                    changes.append((on_time, link, link.quality))
                    graph.add_edge(link.src, link.dst, quality=link.quality)
            if simultaneous:
                bridges = [ link.index for link in graph.bridges()]
                links = list(set(graph.es.indices).difference(bridges))
        # Set graph 'link_changes' = timedelta, src, dst, quality
        self['link_changes'] = []
        current = 0
        for (time, link, quality) in changes:
            change = (delta(seconds=time-current), link.src, link.dst, quality)
            self['link_changes'].append(change)
            current = time
    
    def quality_threshold(self, q):
        pass
    
    
    def bridges(self):
        def number_tree(tree, current, num=-1):
            if current['num'] is None:
                num += 1
                current['num'] = num
                for n in current.successors():
                    num = number_tree(tree, n, num)
            return num
        
        def _bridges_rec(tree, graph, node):
            node['visited'] = True
            lower = node['num']
            higher = node['num']
            descendants = 1
            bridges = []
            for neighbor in node.successors():
                if not neighbor['visited']:
                    bridges.extend(_bridges_rec(tree, graph, neighbor))
                    if neighbor['lower'] < lower:
                        lower = neighbor['lower']
                    if neighbor['higher'] > higher:
                        higher = neighbor['higher']
                    if (neighbor['lower'] == neighbor['num'] and
                            neighbor['higher'] < neighbor['num']+neighbor['descendants']):
                        edge = graph.get_eid(node.index,neighbor.index)
                        bridges.append(graph.es[edge])
                    descendants += neighbor['descendants']
            n_graph = [n.index for n in graph.vs[node.index].successors()]
            n_tree = [n.index for n in node.successors()]
            others =  set(n_graph).difference(n_tree)
            for o in others:
                reachable = tree.vs[o]['num']
                if reachable < lower:
                    lower = reachable
                if reachable > higher:
                    higher = reachable
            node['lower'] = lower
            node['higher'] = higher
            node['descendants'] = descendants
            return bridges
        
        # Get a spanning tree of the graph:
        tree = self.spanning_tree()
        # Transverse in pre-order and number the nodes:
        tree.vs['num'] = None
        number_tree(tree, tree.vs[0])
        # Compute recursively bridges:
        tree.vs['visited'] = False
        return _bridges_rec(tree, self, tree.vs[0])
    
    def set_quality(self, quality=3, edges=None):
        if edges is None:
            edges = self.es
        for edge in edges:
            edge['quality'] = quality() if callable(quality) else quality
    
    def save(self, filename, format='graphml'):
        # Save link_changes
        self.vs['node_id'] = self.vs.indices
        if format != 'pickle':
            try:
                changes = self['link_changes']
                with open('%s_changes' % filename, 'w') as f:
                    for ( time, src, dst, q ) in changes:
                        f.write('%f %i %i %i\n' % (time.total_seconds(), src, dst, q))
            except KeyError:
                pass
        logging.info(self)
        logging.info(filename)
        logging.info(format)
        super(NetworkGraph, self).save(filename, format)
    
    def get_longest_path(self):
        paths = self.shortest_paths_dijkstra()
        max_d = 0
        max_src = None
        max_dst = None
        for (src, distances) in enumerate(paths):
            for (dst, distance) in enumerate(distances):
                if distance > max_d:
                    max_d = distance
                    max_src = src
                    max_dst = dst
        return (max_src, max_dst)

"""
Generate set of graphs:
from networkgraphs import NetworkGraph
import functools
import random
from datetime import timedelta as delta

experiment_length = delta(minutes=30).total_seconds()
#g1 = NetworkGraph.Lattice([4,4])
#g1['name'] = 'Lattice16'


g = NetworkGraph.Static_Power_Law(50, 100, 3)
g['name'] = 'PowerLaw50'
base_q = functools.partial(random.choice, [3,5,7])
g.set_quality(base_q)
base_wait = functools.partial(random.expovariate, 1/30)
base_off = functools.partial(random.expovariate, 0.1/30)
g.random_link_changes(base_wait, base_off, experiment_length)
graphs = []

# Vary frequency
freqs = [1, 10, 20, 30, 40, 50, 60]
for f in freqs:
    wait = functools.partial(random.expovariate, 1/f)
    off = functools.partial(random.expovariate, 0.1/f)
    gr = g.copy()
    gr.random_link_changes(wait, off, experiment_length)
    gr['name'] = g['name'] + ('_base_%i' % f)
    graphs.append(gr)

# Vary link quality
qualities = [3,5,7,9,11,13]
qs = []
for q in qualities:
    qs.append(q)
    quality = functools.partial(random.choice, qs)
    gr = g.copy()
    gr.set_quality(quality)
    gr['name'] = g['name'] + ('_%i_base' % q)
    graphs.append(gr)

for gr in graphs:
    (src,dst) = gr.get_longest_path()
    gr['src'] = src
    gr['dst'] = dst
    gr.save('/tmp/%s.pickle' % gr['name'], format='pickle')
    

# Vary graph size:
sizes = [4,5,6,7,8,9,10]
for size in sizes:
    g1 = NetworkGraph.Lattice([size,size])
    g1['name'] = 'Lattice%i_base' % (size*size)
    g1.set_quality(base_q)
    g1.random_link_changes(base_wait, base_off, experiment_length)
    g2 = NetworkGraph.Static_Power_Law(size*size, size*size*2, 3)
    g2['name'] = 'PowerLaw%i_base' % (size*size)
    g2.set_quality(base_q)
    g2.random_link_changes(base_wait, base_off, experiment_length)
    graphs.extend([g1,g2])

for gr in graphs:
    (src,dst) = gr.get_longest_path()
    gr['src'] = src
    gr['dst'] = dst
    gr.save('/tmp/%s.pickle' % gr['name'], format='pickle')
    

20 of each quality and each frequency:
graphs = []
# Vary frequency
freqs = [1, 10, 20, 30, 40, 50, 60]
for f in freqs:
    for i in range(20):
        wait = functools.partial(random.expovariate, 1/f)
        off = functools.partial(random.expovariate, 0.1/f)
        gr = g.copy()
        gr.random_link_changes(wait, off, experiment_length)
        gr['name'] = g['name'] + ('_base_%i_%i' % (f,i))
        graphs.append(gr)

# Vary link quality
qualities = [3,5,7,9,11,13]
qs = []
for q in qualities:
    for i in range(20):
        qs.append(q)
        quality = functools.partial(random.choice, qs)
        gr = g.copy()
        gr.set_quality(quality)
        gr['name'] = g['name'] + ('_%i_base_%i' % (q,i))
        graphs.append(gr)

for gr in graphs:
    (src,dst) = gr.get_longest_path()
    gr['src'] = src
    gr['dst'] = dst
    gr.save('/tmp/%s.pickle' % gr['name'], format='pickle')

# Generate from a list of already generated with igraph on R:
# On R
# Base graph with 56 nodes:
n.core.nodes <- 266
n.core.links <- 398
rate.o <- 0.01147359
shape.o <- 0.2521602

nodes = 50
links = ceiling(nodes*n.core.links/n.core.nodes)
gr <- build.synthetic.graph(nodes=nodes, links=links, rate=rate.o, shape=shape.o)
write.graph(gr, '/tmp/PowerLawBase.gml', format='gml')

# Generate 10 graphs for each size:
nodes = (5:10)^2

for (node in nodes){
    links = ceiling(node*n.core.links/n.core.nodes)
    for (i in 1:10){
        gr <- build.synthetic.graph(nodes=node, links=links, rate=rate.o, shape=shape.o)
        write.graph(gr, sprintf('/tmp/PowerLaw%i_%i.gml', node, i), format='gml')
    }
}

# On python3 - /tmp/PowerLaw[0-9]*.gml generate size and neighbor graphs:
import sys
from networkgraphs import *
import functools
import random
from datetime import timedelta as delta
import os.path

experiment_length = delta(minutes=30).total_seconds()
files=[sys.argv[i] for i in range(1,len(sys.argv))]

for file in files:
    g = NetworkGraph.Read(file, format='gml')
    g['name'] = os.path.basename(file).split('.')[0]
    (src,dst) = g.get_longest_path()
    g['src']=src
    g['dst']=dst
    base_q = functools.partial(random.choice, [3,5,7])
    g.set_quality(base_q)
    base_wait = functools.partial(random.expovariate, 1/30)
    base_off = functools.partial(random.expovariate, 0.1/30)
    g.random_link_changes(base_wait, base_off, experiment_length)
    g.save('/tmp/%s.pickle' % g['name'], format='pickle')

#On python3 - /tmp/PowerLawBase.gml
import sys
from networkgraphs import *
import functools
import random
from datetime import timedelta as delta
import os.path

#Load base graph
experiment_length = delta(minutes=30).total_seconds()
file = sys.argv[1]
g = NetworkGraph.Read(file, format='gml')
g['name'] = os.path.basename(file).split('.')[0]
(src,dst) = g.get_longest_path()
g['src']=src
g['dst']=dst
base_q = functools.partial(random.choice, [3,5,7])
g.set_quality(base_q)
base_wait = functools.partial(random.expovariate, 1/30)
base_off = functools.partial(random.expovariate, 0.1/30)
g.random_link_changes(base_wait, base_off, experiment_length)

# Frequency:
graphs = []
# Vary frequency
freqs = [1, 10, 20, 30, 40, 50, 60]
for f in freqs:
    for i in range(10):
        wait = functools.partial(random.expovariate, 1/f)
        off = functools.partial(random.expovariate, 0.1/f)
        gr = g.copy()
        gr.random_link_changes(wait, off, experiment_length)
        gr['name'] = g['name'] + ('_base_%i_%i' % (f,i))
        graphs.append(gr)


# Link quality:
qualities = [3,5,7,9,11,13]
qs = []
for q in qualities:
    for i in range(20):
        qs.append(q)
        quality = functools.partial(random.choice, qs)
        gr = g.copy()
        gr.set_quality(quality)
        gr['name'] = g['name'] + ('_%i_base_%i' % (q,i))
        graphs.append(gr)

for gr in graphs:
    (src,dst) = gr.get_longest_path()
    gr['src'] = src
    gr['dst'] = dst
    gr.save('/tmp/%s.pickle' % gr['name'], format='pickle')



>>> import sys
>>> from networkgraphs import *
>>> import functools
>>> import random
>>> from datetime import timedelta as delta
>>> import os.path
>>> 
>>> #Load base graph
... experiment_length = delta(minutes=30).total_seconds()
>>> file = sys.argv[1]
>>> g = NetworkGraph.Read(file, format='gml')
>>> g['name'] = os.path.basename(file).split('.')[0]
>>> (src,dst) = g.get_longest_path()
>>> g['src']=src
>>> g['dst']=dst
>>> base_q = functools.partial(random.choice, [3,5,7])
>>> g.set_quality(base_q)
>>> base_wait = functools.partial(random.expovariate, 1/30)
>>> base_off = functools.partial(random.expovariate, 0.1/30)
>>> g.random_link_changes(base_wait, base_off, experiment_length)
f = 180
wait = functools.partial(random.expovariate, 1/f)
off = functools.partial(random.expovariate, 0.1/f)
gr = g.copy()
gr.random_link_changes(wait, off, experiment_length)
i = 0
gr['name'] = g['name'] + ('_base_%i_%i' % (f,i))
(src,dst) = gr.get_longest_path()
gr['src'] = src
gr['dst'] = dst
gr.save('/tmp/%s.pickle' % gr['name'], format='pickle')
"""

