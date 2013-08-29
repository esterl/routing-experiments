# Class SSHClient is an adaptation of niwibe SSHClient, it can be found at: 
# https://gist.github.com/niwibe/2431088/
import fcntl
import functools
import select
import os
import subprocess
import collections


def isiterable(obj):
    return isinstance(obj, collections.Iterable)


popen = functools.partial(subprocess.Popen, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class SSHClient(object):
    shell = "/bin/sh"
    ssh_path = "/usr/bin/ssh"
    
    started = False
    stopped = False
    returncode = None
    stdout = ""
    stderr = ""
    
    def __init__(self, host, user=None, shell=None, ssh=None): 
        self.stdout = ""
        self.stderr = ""
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
        
        output = []
        while True:
            _r, _w, _e = select.select([_file],[],[], 0.1)
            if len(_r) == 0:
                break
            data = non_block_read(_r[0])
            if data is None or data is b'':
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
        if not self.stopped: self.stderr += self._read(self.proc.stderr)
        return self.stderr
    
    def get_stdout(self):
        if not self.stopped: self.stdout += self._read(self.proc.stdout)
        return self.stdout
    
    def start(self):
        if self.started:
            raise Exception("Already started")
        self._start_ssh_process()
    
    def stop(self):
        if self.stopped:
            raise Exception("Already stopped")
        self.get_stderr()
        self.get_stdout()
        self.stopped = True
        self.proc.terminate()
    
    def execute_background(self, command):
        print('EXECUTE background %s' % command)
        #Run in background:
        if command[-1] != '&':
            command = command + '&'
        n = self.write(command)
        #Read any output that may be waiting
        self.stdout += self._read(self.proc.stdout)
        #Save pid
        command = 'echo $!'
        self.write(command)
        try: 
            self.pid = int(self.proc.stdout.readline())
        except ValueError:
            self.pid=None
        return
    
    def execute_foreground(self, command):
        print('EXECUTE foreground %s' % command)
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


def run(cmd, mlc=True, async=True):
    if mlc:
        cmd = 'cd /home/ester/PhD/mlc; . ./mlc-vars.sh; %s' % cmd
    print('\033[1m$ %s \033[0m' % cmd)
    p = popen(cmd, executable='/bin/bash', shell=True)
    if not async:
        p.wait()
    return p


def ssh(addr, cmd):
    print('\033[1mssh@%s$ %s \033[0m' % (addr, cmd))
    client = SSHClient(addr)
    client.start()
    client.execute(cmd)
    return client
