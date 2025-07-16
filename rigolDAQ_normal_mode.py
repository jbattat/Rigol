import time
import socket
import numpy as np
import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import argparse
import sys
import math


# Define two Rigol scropes and their IPs/ports
SCOPE_CONFIGS = {
    '4804': {'ip': '10.143.0.40', 'port': 5555},
    '5108': {'ip': '10.143.0.59', 'port': 5025}, # was: 10.143.3.150
}

# Global socket
s = None

def send_command(cmd):
    s.sendall(f'{cmd}\n'.encode())

def receive_data(buffer_size=4096):
    return s.recv(buffer_size)

def query(cmd):
    send_command(cmd)
    return receive_data().decode().strip()

def query_float(cmd):
    return float(query(cmd))

def arm_scope():
    send_command(':SING')

def wait_for_trigger(timeout=10):
    start = time.time()
    while True:
        send_command('TRIGger:STATus?')
        status = receive_data().decode().strip()
        if status == 'TD':
            return
        if time.time() - start > timeout:
            raise TimeoutError("Trigger wait timeout.")
        time.sleep(0.001)  


def get_timebase():
    send_command('WAVeform:SOURce CHAN3')  # Any channel
    npts = int(query('WAVeform:POINTs?'))
    xinc = query_float('WAVeform:XINC?')
    xorigin = query_float('WAVeform:XORigin?')
    return xorigin + np.arange(npts) * xinc

def prepare_channel(channel):
    send_command(f'WAVeform:SOURce CHAN{channel}')
    yinc = query_float('WAVeform:YINC?')
    yorigin = query_float('WAVeform:YORigin?')
    yref = query_float('WAVeform:YREFerence?')
    return yinc, yorigin, yref

def get_waveform(channel, yinc, yorigin, yref):
    send_command(f'WAVeform:SOURce CHAN{channel}')
    #time.sleep(0.05)
    send_command('WAVeform:DATA?')
    data = receive_data()

    start = data.find(b'#')
    header_len = int(data[start + 1:start + 2].decode())
    byte_count = int(data[start + 2:start + 2 + header_len].decode())
    waveform_data = data[start + 2 + header_len:]

    while len(waveform_data) < byte_count:
        waveform_data += receive_data(byte_count - len(waveform_data))

    raw = np.frombuffer(waveform_data[:byte_count], dtype=np.uint8)
    voltage = (raw - yorigin - yref) * yinc
    return voltage

# Main routine

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--rigol', choices=SCOPE_CONFIGS, default='5108')
    parser.add_argument('--ntrig', type=int, default=1)
    parser.add_argument('--plot', action='store_true', help="Save PNG plot for each trigger")
    args = parser.parse_args()

    config = SCOPE_CONFIGS[args.rigol]
    ip, port = config['ip'], config['port']

    print(f"Connecting to scope at {ip}:{port}...")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    s.connect((ip, port))

    send_command('*IDN?')
    idn = receive_data().decode().strip()
    print("Connected to scope:", idn)

    send_command('WAVeform:MODE NORMal')
    send_command('WAVeform:FORMat BYTE')
    #send_command('WAVeform:POINts 100')
    #time.sleep(0.1)  # Allow the scope to apply the new setting


    # Get timebase and vertical scaling once
    times = get_timebase()
    yinc3, yorigin3, yref3 = prepare_channel(3)
    yinc4, yorigin4, yref4 = prepare_channel(4)

    # Timestamp prefix for consistent filenames
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")

    # Create output dir
    output_dir = os.path.join(os.path.dirname(__file__), "testRigol")
    os.makedirs(output_dir, exist_ok=True)

    # Save shared timebase
    np.savetxt(os.path.join(output_dir, f"{timestamp}_timebase.csv"), times,
    fmt="%.6e", delimiter=",", header="Time(s)", comments='')

   # Determine digit width based on number of triggers
    digit_width = math.ceil(math.log10(args.ntrig)) if args.ntrig > 1 else 1

    v3_list = []
    v4_list = []

    start_time = time.perf_counter()  # Start timing

    # Set scope to single trigger mode only once
    send_command(':STOP')
    send_command('TRIGger:SWEep SINGle')

    for itrg in range(args.ntrig):
        #print(f"\nTrigger {itrg+1}/{args.ntrig}")
        # arm the scope in single mode for next trigger
        arm_scope()
        wait_for_trigger()

        t1 = time.perf_counter()
        voltage_ch3 = get_waveform(3, yinc3, yorigin3, yref3)
        voltage_ch4 = get_waveform(4, yinc4, yorigin4, yref4)
        t2 = time.perf_counter()
        print(f"Waveform fetch time: {t2 - t1:.4f} s")

        # remove DC offset
        #v3 -= np.mean(v3)
        #v4 -= np.mean(v4)

        v3_list.append(voltage_ch3)
        v4_list.append(voltage_ch4)  

    
    end_time = time.perf_counter()  # End timing
    elapsed = end_time - start_time
    print(f"\nAcquired {args.ntrig} triggers in {elapsed:.3f} seconds")
    print(f"Average time per trigger: {elapsed / args.ntrig:.4f} seconds")

    # Save time, ch3 voltage and ch4 voltage together for each trigger
    for i, (v3, v4) in enumerate(zip(v3_list, v4_list)):
        base = os.path.join(output_dir, f"{timestamp}_trig{i:0{digit_width}d}")
        csv_file = base + ".csv"

        data = np.column_stack((times, v3, v4))
        np.savetxt(csv_file, data, fmt="%.6e", delimiter=",",
               header="Time(s),CH3V,CH4V", comments='')
        print(f"Saved CSV: {csv_file}")

        if args.plot:
            plt.figure(figsize=(10, 4))
            plt.plot(times*1e6, v3*1e3, label='CH3 (Anode)', color='magenta', linewidth=1)
            plt.plot(times*1e6, v4*1e3, label='CH4 (Cathode)', color='blue', linewidth=1)
            plt.xlabel('Time (us)')
            plt.ylabel('Voltage (mV)')
            plt.title(f"{timestamp} Trigger {i}")
            plt.legend()
            plt.grid()
            plt.tight_layout()
            plt.savefig(base + ".png", dpi=150)
            plt.close()
            print(f"Saved plot: {base}.png")

    # All done, close socket
    s.close()
