from playsound3 import playsound


def play():
    """Воспроизведение звукового сигнала"""
    try:
        playsound("sound.mp3", block=True)
    except:
        print("Sound play failed")