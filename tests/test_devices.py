import pytest

from backend.engine.devices import classify


@pytest.mark.parametrize("name,iid,kind,hf", [
    ("Headphones (CMF Buds Pro 2)", r"{1}.BTHENUM\{0000110B-0000-1000-8000-00805F9B34FB}_VID&000102B0", "bluetooth", False),
    ("Headset (CMF Buds Pro 2 Hands-Free)", r"{1}.BTHHFENUM\BTHHFPAUDIO\9&3D4ED94&0&97", "bluetooth", True),
    ("Mi TV (NVIDIA High Definition Audio)", r"{1}.HDAUDIO\FUNC_01&VEN_10DE&DEV_00A7&SUBSYS_103C8DD0", "hdmi", False),
    ("Digital Output (AMD High Definition Audio Device)", r"{1}.HDAUDIO\FUNC_01&VEN_1002&DEV_AA01", "hdmi", False),
    ("Speaker (Realtek(R) Audio)", r"{1}.HDAUDIO\FUNC_01&VEN_10EC&DEV_0245", "builtin", False),
    ("Headphone (Realtek(R) Audio)", r"{1}.HDAUDIO\FUNC_01&VEN_10EC&DEV_0245", "wired", False),
    ("Microphone (USB Audio)", r"{1}.USB\VID_0D8C&PID_0014", "usb", False),
    ("BlackHole 2ch", "", "virtual", False),
    ("MacBook Pro Speakers", "", "builtin", False),
])
def test_classify(name, iid, kind, hf):
    assert classify(name, iid) == (kind, hf)
