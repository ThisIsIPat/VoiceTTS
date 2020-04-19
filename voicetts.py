import io
import logging
import threading
import time
import webbrowser
from queue import Queue
from typing import Callable, Optional

import requests
import soundfile as sf
import speech_recognition as spr

import tkinter as tk
from tkinter import ttk

from pynput import keyboard

print("Please don't close this window. It's necessary for the app to run for a reason I have to figure out myself yet.")

PYAUDIO = spr.Microphone.get_pyaudio().PyAudio()
RECOGNIZER = spr.Recognizer()


def get_devices(predicate: Callable[[dict], bool]):
    global PYAUDIO

    for di in range(PYAUDIO.get_device_count()):
        device = PYAUDIO.get_device_info_by_index(di)
        if predicate(device):
            yield device


def get_device_index(name: str) -> Optional[int]:
    for di in get_devices(lambda device: device["name"] == name):
        return di["index"]
    return None


def get_output_devices():
    return get_devices(lambda device: device["maxOutputChannels"] > 0)


def get_input_devices():
    return get_devices(lambda device: device["maxInputChannels"] > 0)


INPUT_DEVICE_NAME = None
OUTPUT_DEVICE_NAME = None
INPUT_THRESHOLD = 550
TIMEOUT_PREVENTION_FACTOR = 0.5  # Set higher if the script freezes (Can be any positive number)
USER_PTT_BUTTON = None
VTTS_PTT_BUTTON = None


# Buggy method. Ignore
def pipe_indevice_inaudio_ptt(iaq: Queue):
    global PYAUDIO
    global INPUT_DEVICE_NAME

    while True:
        with keyboard.Events() as events:
            for event in events:
                if str(event.key) == USER_PTT_BUTTON and isinstance(event, keyboard.Events.Press):
                    break

        with keyboard.Events() as events:
            device_index = get_device_index(INPUT_DEVICE_NAME)
            device = PYAUDIO.get_device_info_by_index(device_index)
            sample_rate = int(device["defaultSampleRate"])

            stream = PYAUDIO.open(format=PYAUDIO.get_format_from_width(4),
                                  channels=1,
                                  rate=sample_rate,
                                  input=True,
                                  input_device_index=device_index)
            logging.info("Scanning Audio...")
            record_start_time = time.time()

            out_stream = PYAUDIO.open(format=PYAUDIO.get_format_from_width(4),
                                  channels=1,
                                  rate=44100,
                                  output=True,
                                  output_device_index=get_device_index(OUTPUT_DEVICE_NAME))
            frames = []
            for event in events:
                if str(event.key) == USER_PTT_BUTTON and isinstance(event, keyboard.Events.Release):
                    record_end_time = time.time()
                    record_seconds = record_end_time - record_start_time
                    for i in range(0, int(sample_rate / 1024 * record_seconds)):
                        data = stream.read(1024)
                        out_stream.write(data)
                        frames.append(data)
                    stream.close()
                    break
            audio_data = spr.AudioData(b"".join(frames), sample_rate, 4)
            logging.info("Audio recognized. Queueing...")
            iaq.put(audio_data)

            # DEBUGGING...

            out_stream.close()


def pipe_indevice_inaudio_auto(iaq: Queue):
    global INPUT_DEVICE_NAME
    global RECOGNIZER

    while True:
        input_device_index = get_device_index(INPUT_DEVICE_NAME)

        mic = spr.Microphone(device_index=input_device_index)
        with mic as source:
            logging.info("Scanning Audio...")
            audio = RECOGNIZER.listen(source)
            logging.info("Audio recognized. Queueing...")
            if threading.current_thread() != MICROPHONE_THREAD:
                return
            iaq.put(audio)


def pipe_inaudio_text(iaq: Queue, txq: Queue):
    global RECOGNIZER

    while True:
        original_audio = iaq.get()  # type:spr.AudioData
        try:
            interpreted_text = RECOGNIZER.recognize_google(original_audio)
            print('"%s"' % interpreted_text)
            txq.put(interpreted_text)
        except spr.UnknownValueError:
            logging.warning("I couldn't understand that properly. Stop being a Pepega")
        except spr.RequestError as e:
            logging.error(e)


def pipe_text_outaudio(txq: Queue, oaq: Queue):
    global TIMEOUT_PREVENTION_FACTOR

    session = requests.Session()

    while True:
        interpreted_text = txq.get()  # type:str

        tts_req = session.post("https://streamlabs.com/polly/speak",
                               json={'voice': 'Brian', 'text': interpreted_text})

        if tts_req.status_code != 200:
            print("[WARNING] Streamlabs TTS API error")
            continue

        audio_url = tts_req.json()["speak_url"].encode('latin1') \
            .decode('unicode-escape') \
            .encode('latin1') \
            .decode('utf-8')

        time.sleep(TIMEOUT_PREVENTION_FACTOR * 1)  # Timeout prevention

        audio_binary = session.get(audio_url).content
        oaq.put(audio_binary)

        time.sleep(TIMEOUT_PREVENTION_FACTOR * 3)  # Timeout prevention


def pipe_outaudio_outdevice(oaq: Queue):
    global PYAUDIO
    global OUTPUT_DEVICE_NAME
    global VTTS_PTT_BUTTON

    while True:
        audio_binary = oaq.get()

        data, sample_rate = sf.read(io.BytesIO(audio_binary), dtype='float32')
        stream = PYAUDIO.open(format=PYAUDIO.get_format_from_width(4),
                              channels=1,
                              rate=sample_rate,
                              output=True,
                              output_device_index=get_device_index(OUTPUT_DEVICE_NAME))

        frame_width = PYAUDIO.get_sample_size(PYAUDIO.get_format_from_width(4))
        num_frames = int(len(data) / frame_width) * 4  # Have to multiply with ~4. Ah yes, very logical.

        keyboard_controller = keyboard.Controller()
        if VTTS_PTT_BUTTON:
            keyboard_controller.press(VTTS_PTT_BUTTON)

        stream.write(data, num_frames=num_frames)
        stream.close()

        if VTTS_PTT_BUTTON:
            keyboard_controller.release(VTTS_PTT_BUTTON)


def update_device_dropdowns():
    tk_indevice_dropdown["menu"].delete(0, "end")
    for input_device in get_input_devices():
        tk_indevice_dropdown["menu"].add_command(label=input_device["name"],
                                         command=lambda value=input_device["name"]: update_input_device(value))
    tk_outdevice_dropdown["menu"].delete(0, "end")
    for output_device in get_output_devices():
        tk_outdevice_dropdown["menu"].add_command(label=output_device["name"],
                                          command=lambda value=output_device["name"]: update_output_device(value))


def update_mic_ptt():
    global USER_PTT_BUTTON

    with keyboard.Events() as events:
        for event in events:
            if isinstance(event, keyboard.Events.Press):
                USER_PTT_BUTTON = str(event.key)
                tk_mic_ptt_button.configure(text=str(event.key), command=update_mic_ptt)
                return


def update_tts_ptt():
    global VTTS_PTT_BUTTON

    with keyboard.Events() as events:
        for event in events:
            if isinstance(event, keyboard.Events.Press):
                VTTS_PTT_BUTTON = event.key
                tk_tts_ptt_button.configure(text=str(event.key), command=update_tts_ptt)
                return


def update_input_threshold(v):
    global INPUT_THRESHOLD
    global RECOGNIZER
    INPUT_THRESHOLD = int(v)
    RECOGNIZER.energy_threshold = INPUT_THRESHOLD


def update_timeout_prevention(v):
    global TIMEOUT_PREVENTION_FACTOR
    TIMEOUT_PREVENTION_FACTOR = float(v)


def update_input_device(v):
    global INPUT_DEVICE_NAME
    global MICROPHONE_THREAD

    INPUT_DEVICE_NAME = v
    tk_indevice_value.set(v)
    MICROPHONE_THREAD = threading.Thread(target=pipe_indevice_inaudio_auto, args=(iaq,), daemon=True)
    MICROPHONE_THREAD.start()


def update_output_device(v):
    global OUTPUT_DEVICE_NAME
    OUTPUT_DEVICE_NAME = v
    tk_outdevice_value.set(v)


RECOGNIZER.dynamic_energy_threshold = False
RECOGNIZER.energy_threshold = INPUT_THRESHOLD

tk_window = tk.Tk(screenName="VoiceTTS")
tk_indevice_value = tk.StringVar(tk_window)
tk_outdevice_value = tk.StringVar(tk_window)
tk_input_threshold_value = tk.IntVar(tk_window)
tk_timeout_prevention_value = tk.DoubleVar(tk_window)

tk_mic_frame = tk.LabelFrame(tk_window, text="[In] You :)")
tk_tts_frame = tk.LabelFrame(tk_window, text="[Out] TTS")

tk_indevice_dropdown = ttk.OptionMenu(tk_mic_frame, tk_indevice_value)
tk_outdevice_dropdown = ttk.OptionMenu(tk_tts_frame, tk_outdevice_value)

tk_input_threshold_frame = tk.LabelFrame(tk_mic_frame,
                                         text="Input sensitivity")
tk_input_threshold_scale = tk.Scale(tk_input_threshold_frame, variable=tk_input_threshold_value,
                                    from_=150, to=2500, orient=tk.HORIZONTAL, resolution=5, length=300,
                                    command=update_input_threshold)
tk_timeout_prevention_frame = tk.LabelFrame(tk_tts_frame,
                                            text="Timeout prevention")
tk_timeout_prevention_scale = tk.Scale(tk_timeout_prevention_frame, variable=tk_timeout_prevention_value,
                                       from_=0.0, to=5.0, orient=tk.HORIZONTAL, resolution=0.1, length=300,
                                       command=update_timeout_prevention)

tk_update_devices_button = ttk.Button(tk_window, text="Refresh input/output devices", command=update_device_dropdowns)
tk_mic_ptt_frame = tk.LabelFrame(tk_mic_frame, text="Push-To-Talk Button", width="250")
tk_tts_ptt_frame = tk.LabelFrame(tk_tts_frame, text="(Simulated) Push-To-Talk Button", width="250")
tk_mic_ptt_button = tk.Button(tk_mic_ptt_frame, text="Press to setup a button", command=update_mic_ptt)
tk_tts_ptt_button = tk.Button(tk_tts_ptt_frame, text="Press to setup a button", command=update_tts_ptt)
tk_credits_label_frame = tk.LabelFrame(tk_window, text="Stay up-to-date!")
tk_credits_label = tk.Label(tk_credits_label_frame, text="dev.ipat.live", fg="blue", cursor="hand2")
tk_credits_label.bind("<Button-1>", lambda e: webbrowser.open_new("https://dev.ipat.live"))
tk_tcredits_label = tk.Label(tk_window, text="Check me out on Twitch :)", fg="blue", cursor="hand2")
tk_tcredits_label.bind("<Button-1>", lambda e: webbrowser.open_new("http://ipat.live"))


tk_window.resizable(False, False)
tk_window.title("VoiceTTS 0.1.0 by IPat")
tk_window.iconbitmap("MrDestructoid.ico")
tk_indevice_value.set("Select an input device")
tk_outdevice_value.set("Select an output device")
tk_input_threshold_value.set(550)
tk_timeout_prevention_value.set(0.5)
tk_mic_ptt_button.pack()
tk_tts_ptt_button.pack()
tk_input_threshold_scale.pack()
tk_timeout_prevention_scale.pack()

tk_input_threshold_frame.pack()
tk_timeout_prevention_frame.pack()

tk_indevice_dropdown.pack()
tk_outdevice_dropdown.pack()
# tk_mic_ptt_frame.pack() TODO PTT disabled
tk_tts_ptt_frame.pack()

tk_mic_frame.grid(row=0, column=0, sticky=tk.N+tk.S+tk.E+tk.W)
tk_tts_frame.grid(row=0, column=1, sticky=tk.N+tk.S+tk.E+tk.W)
tk_update_devices_button.grid(row=1, column=0, columnspan=2, pady=5, sticky=tk.N+tk.S+tk.E+tk.W)

tk_credits_label.pack()
tk_credits_label_frame.grid(row=2, column=0, columnspan=2, pady=10)
tk_tcredits_label.grid(row=2, column=1, sticky=tk.S+tk.E)

iaq = Queue()
txq = Queue()
oaq = Queue()

# Global Thread variable is not the optimal solution. It causes a minor memory leak.
MICROPHONE_THREAD = threading.Thread(target=pipe_indevice_inaudio_auto, args=(iaq,), daemon=True)
thread_pool = [MICROPHONE_THREAD,
               threading.Thread(target=pipe_inaudio_text, args=(iaq, txq), daemon=True),
               threading.Thread(target=pipe_text_outaudio, args=(txq, oaq), daemon=True),
               threading.Thread(target=pipe_outaudio_outdevice, args=(oaq,), daemon=True)]

for t in thread_pool:
    t.start()

update_device_dropdowns()

tk_window.mainloop()
