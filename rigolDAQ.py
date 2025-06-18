import time
import socket
import numpy as np
import datetime
import matplotlib.pyplot as plt
import os
import argparse
import sys

# Define two Rigol scropes and their IPs/ports
SCOPE_CONFIGS = {
    '4804': {'ip': '10.143.0.40', 'port': 5555},
    '5108': {'ip': '10.143.0.59', 'port': 5025}, # was: 10.143.3.150
}

# Parse command-line arguments
parser = argparse.ArgumentParser(description="DAQ from Rigol DHO Scopes")
parser.add_argument('--rigol', choices=SCOPE_CONFIGS.keys(), required=True, help="Scope model: 4804 or 5108")
parser.add_argument('--ntrig', type=int, required=False, default=10, help="Number of triggers to acquire")

def send_command(cmd):
    s.sendall(f'{cmd}\n'.encode())

def receive_data(buffer_size=4096):
    return s.recv(buffer_size)

def query_float(command):
    send_command(command)
    return float(receive_data().decode())

def get_timebase():
    # FIXME: should get this from the npts = 1000
    npts = query_float('WAVeform:POINTs?')
    time_increment = query_float('WAVeform:XINC?')
    time_origin = query_float('WAVeform:XORigin?')
    time_values = time_origin + np.arange(npts) * time_increment
    return time_values

def get_waveform_data(channel):
    # FIXME: this could go up a level (not per-channel or even per-trigger)
    send_command('WAVeform:MODE NORMal')  # control memory or screen... "maximum" all data on screen in run state
    # but "maximum" when scope is stopped then you get all the wf data
    # "raw" gives full memory, but scope must be stopped
    # So Rigol recommends (1) using "MODE maximum" and (2) stopping before transferring data
    send_command('WAVeform:FORMat BYTE')

    # Channel-specific
    # FIXME: these could move out of the per-trigger level and just be done once per run
    send_command(f'WAVeform:SOURce CHAN{channel}')
    voltage_increment = query_float('WAVeform:YINC?')
    voltage_origin = query_float('WAVeform:YORigin?')
    voltage_reference = query_float('WAVeform:YREFerence?')

    send_command('WAVeform:DATA?')
    data = receive_data()
    start_idx = data.find(b'#')
    if start_idx == -1:
        raise ValueError("Data block not found")

    header_length = int(data[start_idx + 1:start_idx + 2].decode())
    byte_count = int(data[start_idx + 2:start_idx + 2 + header_length].decode())
    waveform_data = data[start_idx + 2 + header_length:start_idx + 2 + header_length + byte_count] 

    waveform = np.frombuffer(waveform_data, dtype=np.uint8)
    voltage_values = (waveform - voltage_origin - voltage_reference) * voltage_increment
    
    return voltage_values


def arm_scope():
    send_command('TRIGger:SWEep SINGle')

    
def get_trigger_status():
    send_command('TRIGger:STATus?')
    status = receive_data().decode('utf-8').strip()
    return status

if __name__ == "__main__":
    args = parser.parse_args()

    # Number of triggers to acquire
    n_trigs = args.ntrig
    print(f"Will acquire {n_trigs} triggers")
    
    # Create socket and connect
    config = SCOPE_CONFIGS[args.rigol]
    ip, port = config['ip'], config['port']
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((ip, port))
    
    # Identify the scope
    send_command('*IDN?')
    print("Scope identity:", receive_data().decode())
    
    # Output data location
    output_dir = os.path.join(os.path.dirname(__file__), "testRigol") # Change to your desired directory path to save data
    os.makedirs(output_dir, exist_ok=True)
    print(f"Data will be saved in this directory: {output_dir}")

    # Acquire a series of waveforms with the same time-base
    times = get_timebase()

    arm_scope() # set to SINGLE mode
    for itrg in range(n_trigs):
        print(f"\nAcquiring trigger {itrg:05d}/{n_trigs:05d}")

        # Wait until the scope is triggered...
        while True:
            if get_trigger_status() == 'TD':
                break
            time.sleep(0.1)
            
        # ... then acquire data from channels 3 and 4
        voltage_ch3 = get_waveform_data(3)
        voltage_ch4 = get_waveform_data(4)

        # arm the scope in single mode for next trigger
        arm_scope()

        # Save data
        timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        csv_filename = os.path.join(output_dir, f"{timestamp}.csv")

        try:
            data_to_save = np.column_stack((times, voltage_ch3, voltage_ch4))
            np.savetxt(csv_filename, data_to_save, fmt="%.6e", delimiter=",", header="Time(s),CH3V,CH4V", comments='')
            print(f"WF data saved to {csv_filename}.")
        except:
            print("Error trying to save data...")
            print(f"len(time), len(v3), len(v4) = {len(times)}, {len(voltage_ch3)}, {len(voltage_ch4)}")
    
    # All done, close socket
    s.close()
    
