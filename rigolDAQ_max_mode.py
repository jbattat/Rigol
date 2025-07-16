import time
import socket
import numpy as np
import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
import argparse
import math

# Rigol 5108
IP = '10.143.0.59'
PORT = 5025
TIMEOUT = 20

# Global socket
s = None  

def send_command(cmd):
    s.sendall(f'{cmd}\n'.encode())

def receive_data(buffer_size=4096):
    s.settimeout(0.1)  
    chunks = []
    try:
        while True:
            chunk = s.recv(buffer_size)
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < buffer_size:
                break
    except socket.timeout:
        pass  # Avoid hanging forever
    return b''.join(chunks)

def receive_waveform_data(byte_count, buffer_size=4096):
    received = b''
    while len(received) < byte_count:
        chunk = s.recv(min(buffer_size, byte_count - len(received)))
        if not chunk:
            raise ConnectionError("Connection lost while receiving waveform data.")
        received += chunk
    return received

def query_float(command, retries=3):
    for i in range(retries):
        send_command(command)
        try:
            resp = receive_data().decode().strip()
            if resp:
                return float(resp)
        except Exception as e:
            print(f"Warning: Failed to get float for '{command}' (attempt {i+1}): {e}")
        time.sleep(0.01)
    raise RuntimeError(f"Failed to get valid float response for command: {command}")

def prepare_channel_scaling(channel):
    send_command(f'WAVeform:SOURce CHAN{channel}')
    time.sleep(0.03)
    yinc = query_float('WAVeform:YINC?')
    yorigin = query_float('WAVeform:YORigin?')
    yref = query_float('WAVeform:YREFerence?')
    return yinc, yorigin, yref

def get_waveform_data(channel, yinc, yorigin, yref):
    send_command(f'WAVeform:SOURce CHAN{channel}')
    #time.sleep(0.01)

    for attempt in range(3):
        send_command('WAVeform:DATA?')
        data = receive_data()
        start = data.find(b'#')
        if start != -1:
            break
        print(f"Warning: no waveform data header on CH{channel}, retrying ({attempt+1}/3)...")
        time.sleep(0.1)
    else:
        raise ValueError(f"No waveform data header found for channel {channel}")

    header_len = int(data[start + 1:start + 2].decode())
    byte_count = int(data[start + 2:start + 2 + header_len].decode())
    waveform_data = data[start + 2 + header_len:]
    if len(waveform_data) < byte_count:
        waveform_data += receive_waveform_data(byte_count - len(waveform_data))

    raw = np.frombuffer(waveform_data[:byte_count], dtype=np.uint8)
    voltage = (raw - yorigin - yref) * yinc
    return voltage

def flush_socket():
    s.setblocking(False)
    try:
        while True:
            _ = s.recv(4096)
    except BlockingIOError:
        pass
    finally:
        s.setblocking(True)

def get_trigger_status():
    send_command('TRIGger:STATus?')
    status = receive_data().decode('utf-8').strip()

    return status

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rigol Scope Data Acquisition")
    parser.add_argument('--ntrig', type=int, default=1, help="Number of triggers to acquire")
    parser.add_argument('--plot', action='store_true', help="Save PNG plot for each trigger")
    args = parser.parse_args()

    output_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "testRigol")
    os.makedirs(output_dir, exist_ok=True)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    
    print("Connecting to scope...")
    s.connect((IP, PORT))
    send_command('*IDN?')
    idn = receive_data().decode().strip()
    print(f"Connected to scope: {idn}")
    
    # Set memory depth and waveform configuration ONCE
    #send_command(':ACQuire:MEMDepth LONG')
    send_command('WAVeform:MODE MAXimum')   
    send_command('WAVeform:FORMat BYTE')    

    time.sleep(0.02)

    # Timebase setup using CH3 (or any available channel)
    send_command('WAVeform:SOURce CHAN3')
    xinc = query_float('WAVeform:XINC?')
    xorigin = query_float('WAVeform:XORigin?')
    send_command('WAVeform:POINTs?')
    time.sleep(0.02)
    npts = int(receive_data().decode().strip())
    time_array = xorigin + np.arange(npts) * xinc
    #print(f"Timebase prepared once: xinc={xinc}, xorigin={xorigin}, npts={npts}")
    
    # Vertical scaling for both channels
    yinc3, yorigin3, yref3 = prepare_channel_scaling(3)
    yinc4, yorigin4, yref4 = prepare_channel_scaling(4)
    #print("Vertical scaling prepared once for channels 3 and 4.")

    # Determine digit width based on number of triggers
    digit_width = math.ceil(math.log10(args.ntrig)) if args.ntrig > 1 else 1
    
    # Prepare containers for waveforms
    v3_list = []
    v4_list = []

    start_time = time.perf_counter()  # Start timing
    
    # Create a timestamp for filenames
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")

    # Set scope to single trigger mode only once
    send_command(':STOP')
    send_command('TRIGger:SWEep SINGle')

    for itrg in range(args.ntrig):
        #print(f"\nAcquiring trigger {itrg+1}/{args.ntrig}")
        flush_socket()
  
        time.sleep(0.05)  # Allow scope to settle
        send_command(':SINGle')

        trigger_start = time.perf_counter() 
    
        while True:
            if get_trigger_status() == 'TD':
                break
            if time.perf_counter() - trigger_start > 10:
                raise TimeoutError("Timeout waiting for trigger")
            time.sleep(0.1)

        send_command(':STOP')
        time.sleep(0.05)
   
        t1 = time.perf_counter()
        
        # ... then acquire data from channels 3 and 4
        voltage_ch3 = get_waveform_data(3, yinc3, yorigin3, yref3)
        flush_socket()
        voltage_ch4 = get_waveform_data(4, yinc4, yorigin4, yref4)
       
        t2 = time.perf_counter()
        print(f"Waveform fetch time: {t2 - t1:.4f} s")
        
        v3_list.append(voltage_ch3)
        v4_list.append(voltage_ch4)

    end_time = time.perf_counter()  # End timing
    elapsed = end_time - start_time
    print(f"\nAcquired {args.ntrig} triggers in {elapsed:.3f} seconds")
    print(f"Average time per trigger: {elapsed / args.ntrig:.4f} seconds")

    # Save time, ch3 voltage and ch4 voltage together for each trigger
    for i, (v3, v4) in enumerate(zip(v3_list, v4_list)):
        base = os.path.join(output_dir, f"{timestamp}_trig{i:0{digit_width}d}")
        csv_path = base + ".csv"
        np.savetxt(csv_path, np.column_stack((time_array, v3, v4)), fmt="%.6e", delimiter=",",
                   header="Time(s),CH3V,CH4V", comments='')
        print(f"Saved CSV: {csv_path}")

        if args.plot:
            plt.figure(figsize=(8, 4))
            plt.plot(time_array * 1e6, v3 * 1e3, label='CH3 (Anode)', color='magenta')
            plt.plot(time_array * 1e6, v4 * 1e3, label='CH4 (Cathode)', color='blue')
            plt.xlabel("Time (μs)")
            plt.ylabel("Voltage (mV)")
            plt.title(f"{timestamp} Trigger {i}")
            plt.grid()
            plt.legend()
            plt.tight_layout()
            plt.savefig(base + ".png", dpi=150)
            plt.close()
            print(f"Saved plot: {base}.png")

    # All done, close socket
    s.close()


