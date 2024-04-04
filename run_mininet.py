# the code uses network topology that uses switches, links and hosts
from mininet.topo import Topo  # topo defines network topology. topological representation shows interconnection of hosts, switches and links in a simulated network.
from mininet.net import Mininet # mininet class is a key component that allows you to create and manage a simulated network environment.
from mininet.link import TCLink # TCLink class represents a link between mininet hosts and switches; provides a way to specify characteristics like bandwidth, delay and loss for a link.
from mininet.log import setLogLevel # to set logging levels for the mininet output messages. logging levels are a way to categorize and prioritize log messages.
from mininet.cli import CLI # CLI = Command Line Interface for interacting with the mininet network and running commands on network devices.
from mininet.clean import cleanup   # used to clean up the resources after the completion of mininet script.

from helper.util import print_error, print_warning, print_success, colorize, print_line
from helper.util import get_git_revision_hash, get_host_version, get_available_algorithms, check_tools, check_tool
from helper.util import sleep_progress_bar
from helper.util import compress_file
from helper import BUFFER_FILE_EXTENSION, FLOW_FILE_EXTENSION, COMPRESSION_METHODS, TEXT_WIDTH

import os   # provides a module provides a way to interact with the operating system.
import sys  
import subprocess   # module used to interact with the system's shell and executing external commands.
import time # time module for initializing time
import argparse # to parse command line argument for a python program
import re   # model to import regular expression
import glob # to find files that match a specified list of files in a directory


MAX_HOST_NUMBER = 256**2    # constant variable used to threshold or limit the number of hosts that can be added to the network topology


class DumbbellTopo(Topo):   # inherit topo from the mininet class
    # Three switchs connected to n senders and receivers.

    def build(self, n=2):   # to build a network topology
        switch1 = self.addSwitch('s1')  #addSwitch => to add switch to the topology and assign variable 'switch1'
        switch2 = self.addSwitch('s2')  
        switch3 = self.addSwitch('s3')

        self.addLink(switch1, switch2)  #addLink => adds link betweeen switch1 and switch2
        self.addLink(switch2, switch3)  #addLink => adds link betweeen switch2 and switch3

        for h in range(n):  # to make host and receiver connect to the topology
            host = self.addHost('h%s' % h, cpu=.5 / n)  #addHost => add hosts to the topology and assigns it to the host
            self.addLink(host, switch1) # cpu = 0.5 / n is used to set cpu weights hosts in the mininet topology. 
            receiver = self.addHost('r%s' % h, cpu=.5 / n)  
            self.addLink(receiver, switch3)


def parseConfigFile(file):   
    cc_algorithms = str(get_available_algorithms())

    unknown_alorithms = []  
    number_of_hosts = 0
    output = []
    f = open(file)  # open the config file to read values from it.
    for line in f:  #  iterate over each line in the file
        line = line.replace('\n', '').strip()   # remove newline characters and leading/trailing whitespaces

        if len(line) > 1:   # skip empty lines and lines with "#"
            if line[0] == '#':
                continue

        split = line.split(',') # split the line into components using the comma as a delimiter
        if split[0] == '':
            continue
        command = split[0].strip()

        if command == 'host':
            # parse host commands that checks if the cubic congestion control algorithm is available and if the hosts has reached maximum number.
            if len(split) != 5:
                print_warning('Too few arguments to add host in line\n{}'.format(line)) # to ensure that the host command has correct number of arguments.
                continue
            algorithm = split[1].strip()
            rtt = split[2].strip()
            start = float(split[3].strip())
            stop = float(split[4].strip())
            # to check if the congestion control algorithm is available or not
            if algorithm not in cc_algorithms:
                if algorithm not in unknown_alorithms:
                    unknown_alorithms.append(algorithm)
                continue

            # check if the maximum number of hosts is reached
            if number_of_hosts >= MAX_HOST_NUMBER:
                print_warning('Max host number reached. Skipping further hosts.')
                continue

            # increment the number of hosts and add the host configuration to the output
            number_of_hosts += 1
            output.append({ # to define the host command
                'command': command,
                'algorithm': algorithm, # congestion control algorithm
                'rtt': rtt, # round trip time of the host
                'start': start, # start time for the host activity
                'stop': stop})  # stop time for the host activity

        elif command == 'link':
            # parse link commands is used to process and interpret configuarations of the network
            if len(split) != 4:
                print_warning('Too few arguments to change link in line\n{}'.format(line))
                continue
            change = split[1].strip()
            # check if the link change option is valid
            if change != 'bw' and change != 'rtt' and change != 'loss':
                print_warning('Unknown link option "{} in line\n{}'.format(change, line))
                continue
            value = split[2].strip()
            start = float(split[3].strip())
            # add link configuration to the option
            output.append({ # to define the link command
                'command': command,
                'change': change,   # parameter to be changed in the link configuration
                'value': value, # set new value for the specified parameter
                'start': start  # the start time for the link configuration change
            })
        else:
            print_warning('Skip unknown command "{}" in line\n{}'.format(command, line))
            continue

    # if there are unknown algorithm, print a warning and give a timeout option to proceed
    if len(unknown_alorithms) > 0:
        print_warning('Skipping uninstalled congestion control algorithm:\n  ' + ' '.join(unknown_alorithms))
        print_warning('Available algorithms:\n  ' + cc_algorithms.strip())
        print_warning('Start Test anyway in 10s. (Press ^C to interrupt)')
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            sys.exit(1)

    return output


def traffic_shaping(mode, interface, add, **kwargs):   # traffic shaping is applied to manage bandwidth and improve network performance.
    # **kwargs =>  A variable number of keyword arguments representing specific parameters based on the traffic shaping mode
    if mode == 'tbf':   # one of the traffic shapping methods is Token Bucket Filter. Allows bursts of data to be sent at higher rate than the average rate but the rate can be sustained using the token bucket
        command = 'tc qdisc {} dev {} root handle 1: tbf rate {} buffer {} latency {}'.format('add' if add else ' change',
                                                                                    interface, kwargs['rate'],
                                                                                    kwargs['buffer'], kwargs['latency'])
    elif mode == 'netem':   # Network Emulator is one of the Traffic Shapping methods. It uses controlled delays, packet loss, duplication, and reordering to simulate various network conditions.
        command = 'tc qdisc {} dev {} parent 1: handle 2: netem delay {} loss {}'.format('add' if add else ' change',
                                                                          interface, kwargs['delay'], kwargs['loss'])
    return command


def run_test(commands, output_directory, name, bandwidth, initial_rtt, initial_loss,
             buffer_size, buffer_latency, poll_interval):

    duration = 0
    start_time = 0
    number_of_hosts = 0

    current_netem_delay = initial_rtt   # initial values of network delay 
    current_netem_loss = initial_loss   # initial values of network loss

    if not os.path.exists(output_directory):
        os.makedirs(output_directory)

    config = [  # configure the final setup for the process
        'Test Name: {}'.format(name),  # represents the name of the test file
        'Date: {}'.format(time.strftime('%c')), # date and time of the test being run
        'Kernel: {}'.format(get_host_version()),    # to retrieve the host information
        'Git Commit: {}'.format(get_git_revision_hash()),   # includes the git commit hast of the cadebase
        'Initial Bandwidth: {}'.format(bandwidth),  # initial bandwidth used for the test. variable bandwidth holds this information
        'Burst Buffer: {}'.format(buffer_size), # holds the burst buffer size(to alleviate I/O bottlenecks by providing temporary high speed storage space), buffer_size holds this value 
        'Buffer Latency: {}'.format(buffer_latency),    # Buffer latency is the time delay introduced when data is transferred. buffer_latency stores the this value
        'Initial Link RTT: {}'.format(initial_rtt),
        'Initial Link Loss: {}'.format(initial_loss),
        'Commands: '
    ]
    for cmd in commands:
        start_time += cmd['start']  # to track the cumulative time at which each command starts in test

        config_line = '{}, '.format(cmd['command'])
        if cmd['command'] == 'link':    # includes additional parameters in order to change properties of a link between the switched
            config_line += '{}, {}, {}'.format(cmd['change'], cmd['value'], cmd['start'])
        elif cmd['command'] == 'host':  # includes additional parameters 
            number_of_hosts += 1    # to increment the number of hosts that have been used
            config_line += '{}, {}, {}, {}'.format(cmd['algorithm'], cmd['rtt'], cmd['start'], cmd['stop'])
            if start_time + cmd['stop'] > duration:
                duration = start_time + cmd['stop']
        config.append(config_line)

    with open(os.path.join('{}'.format(output_directory), 'parameters.txt'), 'w') as f:
        f.write('\n'.join(config))  # creates the parameters.txt that contains the details included in config list

    print('-' * TEXT_WIDTH) # To print the duaration of start test and the total duration
    print('Starting test: {}'.format(name))
    print('Total duration: {}s'.format(duration))

    try:    # to start mininet network to perform the required test
        topo = DumbbellTopo(number_of_hosts)    # to create network topology and specify the number of hosts
        net = Mininet(topo=topo, link=TCLink)   # create a mininet network and link type TCLink
        net.start() # start mininet network using the start method
    except Exception as e:
        print_error('Could not start Mininet:')
        print_error(e)
        sys.exit(1)

    # to start tcp dump => is a packet analyzer that allows to display TCP and UDP packets being transmitted to the network topologies
    try:
        FNULL = open(os.devnull, 'w')
        subprocess.Popen(['tcpdump', '-i', 's1-eth1', '-n', 'tcp', '-s', '88',
                          '-w', os.path.join(output_directory, 's1.pcap')], stderr=FNULL)
        subprocess.Popen(['tcpdump', '-i', 's3-eth1', '-n', 'tcp', '-s', '88',
                          '-w', os.path.join(output_directory, 's3.pcap')], stderr=FNULL)
    except Exception as e:
        print_error('Error on starting tcpdump\n{}'.format(e))
        sys.exit(1)


    time.sleep(1)

    host_counter = 0
    for cmd in commands:    # to perform operartions on the host
        if cmd['command'] != 'host':
            continue
        send = net.get('h{}'.format(host_counter))  # to assign ip addresses to hosts. Each host has a send and recv nodes to assign ip addresses in the form 10.1. and 10.2 subnets
        send.setIP('10.1.{}.{}/8'.format(host_counter // 256, host_counter % 256))
        recv = net.get('r{}'.format(host_counter))
        recv.setIP('10.2.{}.{}/8'.format(host_counter // 256, host_counter % 256))
        host_counter += 1

        # setup FQ, algorithm, netem, nc host, to set up traffic at both sender and receiver end and this includes Fair Queueing pacing for sender and applies netem delay for the receiver
        send.cmd('tc qdisc add dev {}-eth0 root fq pacing'.format(send))
        send.cmd('ip route change 10.0.0.0/8 dev {}-eth0 congctl {}'.format(send, cmd['algorithm']))
        send.cmd('ethtool -K {}-eth0 tso off'.format(send))
        recv.cmd('tc qdisc add dev {}-eth0 root netem delay {}'.format(recv, cmd['rtt']))
        recv.cmd('timeout {} nc -klp 9000 > /dev/null &'.format(duration))  # to start tcp server on receiver

        # pull BBR values
        send.cmd('./ss_script.sh {} >> {}.{} &'.format(poll_interval, os.path.join(output_directory, send.IP()), FLOW_FILE_EXTENSION))  # this command captures the BBR (Bottleneck Bandwidth and Round-trip propagation time) values periodically

    s2, s3 = net.get('s2', 's3')    # traffic shapping in switch 2 and 3.
    s2.cmd(traffic_shaping('tbf', 's2-eth2', add=True, rate=bandwidth, buffer=buffer_size, latency=buffer_latency))

    netem_running = False   # netem configuration on switch 2
    if current_netem_delay != '0ms' or current_netem_loss != '0%':
        netem_running = True
        s2.cmd(traffic_shaping('netem', 's2-eth2', add=True, delay=current_netem_delay, loss=current_netem_loss))   # start buffer monitering script in switch 2
    s2.cmd('./buffer_script.sh {0} {1} >> {2}.{3} &'.format(poll_interval, 's2-eth2',
                                                            os.path.join(output_directory, 's2-eth2-tbf'),
                                                            BUFFER_FILE_EXTENSION))

    complete = duration
    current_time = 0
    host_counter = 0


    try:    # to execute the specified commands, configure the network, logs information and handles exceptions
        for cmd in commands:
            start = cmd['start']
            current_time = sleep_progress_bar(start, current_time=current_time, complete=complete)

            if cmd['command'] == 'link':
                s2 = net.get('s2')

                if cmd['change'] == 'bw':
                    s2.cmd(traffic_shaping('tbf', 's2-eth2', add=False, rate=cmd['value'], buffer=buffer_size, latency=buffer_latency))
                    log_String = '  Change bandwidth to {}.'.format(cmd['value'])

                elif cmd['change'] == 'rtt' or cmd['change'] == 'loss':
                    current_netem_delay = cmd['value'] if cmd['change'] == 'rtt' else current_netem_delay
                    current_netem_loss = cmd['value'] if cmd['change'] == 'loss' else current_netem_loss

                    s2.cmd(traffic_shaping('netem', 's2-eth2', add=not netem_running, delay=current_netem_delay,
                                           loss=current_netem_loss))
                    netem_running = True
                    log_String = '  Change {} to {}.'.format(cmd['change'], cmd['value'])

            elif cmd['command'] == 'host':
                send = net.get('h{}'.format(host_counter))
                recv = net.get('r{}'.format(host_counter))
                timeout = cmd['stop']
                log_String = '  h{}: {} {}, {} -> {}'.format(host_counter, cmd['algorithm'], cmd['rtt'], send.IP(), recv.IP())
                send.cmd('timeout {} nc {} 9000 < /dev/urandom > /dev/null &'.format(timeout, recv.IP()))
                host_counter += 1
            print(log_String + ' ' * (TEXT_WIDTH - len(log_String)))

        current_time = sleep_progress_bar((complete - current_time) % 1, current_time=current_time, complete=complete)  # the two lines is the simlulate the progression of time for the test execution. 
        current_time = sleep_progress_bar(complete - current_time, current_time=current_time, complete=complete)    # sleep_progress_bar is to control the timing and visualize the progress of the test
    except (KeyboardInterrupt, Exception) as e:
        if isinstance(e, KeyboardInterrupt):
            print_warning('\nReceived keyboard interrupt. Stop Mininet.')
        else:
            print_error(e)
    finally:
        net.stop()
        cleanup()

    print('-' * TEXT_WIDTH)


def verify_arguments(args, commands):   # to check if the input arguments and commands for a network tests are valid. checks on various parameters such as bandwidth, rtt, loss percentage, buffer size and latency
    verified = True

    verified &= verify('rate', args.bandwidth)
    verified &= verify('time', args.rtt)
    verified &= verify('percent', args.loss)
    verified &= verify('size', args.buffer_size)
    verified &= verify('time', args.latency)

    for c in commands:
        if c['command'] == 'link':
            if c['change'] == 'bw':
                verified &= verify('rate', c['value'])
            elif c['change'] == 'rtt':
                verified &= verify('time', c['value'])
            elif c['change'] == 'loss':
                verified &= verify('percent', c['value'])
        elif c['command'] == 'host':
            verified &= verify('time', c['rtt'])

    return verified


def verify(type, value):    # to verify the format of numerical vlaues with units
    if type == 'rate':
        allowed = ['bit', 'kbit', 'mbit', 'bps', 'kbps', 'mbps']
    elif type == 'time':
        allowed = ['s', 'ms', 'us']
    elif type == 'size':
        allowed = ['b', 'kbit', 'mbit', 'kb', 'k', 'mb', 'm']
    elif type == 'percent':
        allowed = ['%']
    else:
        allowed = []  # Unknown type

    si = re.sub('^([0-9]+\.)?[0-9]+', '', value).lower()

    if si not in allowed:
        print_error('Malformed {} unit: {} not in {}'.format(type, value, list(allowed)))
        return False
    return True


def compress_output(dir, method):   # obtain the compressed files along with parameters.txt to analyze and visualize the results obtained after performing the algorithm

    all_files = glob.glob(os.path.join(dir, '*.{}'.format(FLOW_FILE_EXTENSION)))
    all_files += glob.glob(os.path.join(dir, '*.{}'.format(BUFFER_FILE_EXTENSION)))
    all_files += glob.glob(os.path.join(dir, '*.pcap'))

    print('Compressing files:')

    for f in all_files:
        compress_file(f, method)
        print('  * {}'.format(os.path.basename(f)))


if __name__ == '__main__':
    if check_tools() > 0:
        exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument('config', metavar='CONFIG',
                        help='Path to the config file.')
    parser.add_argument('-b', dest='bandwidth',
                        default='10Mbit', help='Initial bandwidth of the bottleneck link. (default: 10mbit)')
    parser.add_argument('-r', dest='rtt',
                        default='0ms', help='Initial rtt for all flows. (default 0ms)')
    parser.add_argument('--loss', dest='loss',
                        default='0%', help='Initial rtt for all flows. (default 0%)')
    parser.add_argument('-d', dest='directory',
                        default='test/', help='Path to the output directory. (default: test/)')
    parser.add_argument('-s', dest='buffer_size',
                        default='1600b', help='Burst size of the token bucket filter. (default: 1600b)')
    parser.add_argument('-l', dest='latency',
                        default='100ms', help='Maximum latency at the bottleneck buffer. (default: 100ms)')
    parser.add_argument('-n', dest='name',
                        help='Name of the output directory. (default: <config file name>)')
    parser.add_argument('--poll-interval', dest='poll_interval', type=float,
                        default=0.04, help='Interval to poll TCP values and buffer backlog in seconds. (default: 0.04)')
    parser.add_argument('-c --compression', dest='compression',
                        choices=COMPRESSION_METHODS, default=COMPRESSION_METHODS[1],
                        help='Compression method of the output files. Default: {}'.format(COMPRESSION_METHODS[1]))

    args = parser.parse_args()

    # Use config file name if no explicit name is specified
    args.name = args.name or os.path.splitext(os.path.basename(args.config))[0]

    if not os.path.isfile(args.config):
        print_error('Config file missing: {}'.format(args.config))
        sys.exit(128)

    commands = parseConfigFile(args.config)
    if len(commands) == 0:
        print_error('No valid commands found in config file.')
        sys.exit(128)

    if not verify_arguments(args, commands):
        print_error('Please fix malformed parameters.')
        sys.exit(128)

    output_directory = os.path.join(args.directory, '{}_{}'.format(time.strftime('%m%d_%H%M%S'), args.name))

    # setLogLevel('info')
    run_test(bandwidth=args.bandwidth,
             initial_rtt=args.rtt,
             initial_loss=args.loss,
             commands=commands,
             buffer_size=args.buffer_size,
             buffer_latency=args.latency,
             name=args.name,
             output_directory=output_directory,
             poll_interval=args.poll_interval)

    compression = args.compression

    if compression != COMPRESSION_METHODS[0]:
        if not check_tool(compression):
            print_warning('Compression with {} not possible. Continuing without compression.'.format(compression))
            compression = COMPRESSION_METHODS[0]

        compress_output(output_directory, compression)
        print('-' * TEXT_WIDTH)