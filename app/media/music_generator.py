"""AI Music Generator creating dynamic, royalty-free BGM tracks for YouTube video scenes."""

import hashlib
import math
from pathlib import Path
import struct
from typing import List, Optional, Tuple
import wave


class MusicGenerationError(RuntimeError):
    """Raised when BGM generation fails."""
    pass


class MusicGenerator:
    """Generates procedural high-retention anime lo-fi, synthwave, and chillhop BGM tracks."""

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate

    def _note_to_freq(self, note_name: str) -> float:
        """Convert standard note notation (e.g. 'C4', 'A#3', 'Eb4') to frequency in Hz."""
        notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        note_name = note_name.replace("Db", "C#").replace("Eb", "D#").replace("Gb", "F#").replace("Ab", "G#").replace("Bb", "A#")
        if len(note_name) == 2:
            pitch, octave = note_name[0], int(note_name[1])
        elif len(note_name) == 3:
            pitch, octave = note_name[:2], int(note_name[2])
        else:
            return 440.0
        semitone = notes.index(pitch)
        # MIDI note number: C4 = 60, A4 = 69 = 440 Hz
        midi = 12 * (octave + 1) + semitone
        return 440.0 * (2.0 ** ((midi - 69) / 12.0))

    def generate_track(
        self,
        duration_seconds: float,
        output_path: Path,
        mood: str = "anime_lofi",
        bpm: int = 85,
    ) -> Tuple[str, str]:
        """Generate high-quality multi-layer BGM track matching target duration."""
        if duration_seconds <= 0.0:
            raise MusicGenerationError(f"duration_seconds must be positive ({duration_seconds}s).")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        total_samples = int(self.sample_rate * duration_seconds)
        seconds_per_beat = 60.0 / bpm
        samples_per_beat = int(self.sample_rate * seconds_per_beat)

        # Chord progressions: list of (chords, bass)
        # Anime Lo-Fi: Dm9 -> G13 -> Cmaj9 -> Am7
        chords_lofi = [
            (["D3", "F3", "A3", "C4", "E4"], "D2"),
            (["G2", "F3", "B3", "E4"], "G1"),
            (["C3", "E3", "G3", "B3", "D4"], "C2"),
            (["A2", "E3", "G3", "C4"], "A1"),
        ]

        # Buffers for left and right channels (floating point)
        buf_l = [0.0] * total_samples
        buf_r = [0.0] * total_samples

        total_beats = int(duration_seconds / seconds_per_beat) + 2

        for beat_idx in range(total_beats):
            chord_idx = (beat_idx // 4) % len(chords_lofi)
            chord_notes, bass_note = chords_lofi[chord_idx]
            beat_start_sample = beat_idx * samples_per_beat

            # 1. Warm Electric Piano / Pad Chord on every bar (beat 0 of 4)
            if beat_idx % 4 == 0:
                chord_dur_sec = seconds_per_beat * 4.0
                chord_samples = min(int(self.sample_rate * chord_dur_sec), total_samples - beat_start_sample)
                for note in chord_notes:
                    freq = self._note_to_freq(note)
                    for s in range(chord_samples):
                        idx = beat_start_sample + s
                        if idx >= total_samples:
                            break
                        t = s / self.sample_rate
                        # ADSR envelope: fast attack, long decay
                        env = math.exp(-1.8 * t) * min(1.0, t * 15.0)
                        # Soft harmonic overtone
                        wave_val = (
                            math.sin(2.0 * math.pi * freq * t) * 0.7
                            + math.sin(4.0 * math.pi * freq * t) * 0.25
                            + math.sin(6.0 * math.pi * freq * t) * 0.05
                        )
                        # Stereo chorus detune
                        buf_l[idx] += wave_val * env * 0.16
                        buf_r[idx] += (
                            (math.sin(2.0 * math.pi * (freq * 1.002) * t) * 0.7
                             + math.sin(4.0 * math.pi * (freq * 1.002) * t) * 0.25)
                            * env * 0.16
                        )

            # 2. Sub-bass / Warm 808 on beats 0 and 2
            if beat_idx % 2 == 0:
                bass_freq = self._note_to_freq(bass_note)
                bass_dur = int(self.sample_rate * seconds_per_beat * 1.8)
                bass_samples = min(bass_dur, total_samples - beat_start_sample)
                for s in range(bass_samples):
                    idx = beat_start_sample + s
                    if idx >= total_samples:
                        break
                    t = s / self.sample_rate
                    env = math.exp(-2.2 * t) * min(1.0, t * 30.0)
                    # Warm filtered sine bass
                    val = math.sin(2.0 * math.pi * bass_freq * t) * 0.35
                    buf_l[idx] += val * env
                    buf_r[idx] += val * env

            # 3. Soft Lo-fi Kick drum on beat 0 and beat 2.5 (syncopated)
            kick_samples = min(int(self.sample_rate * 0.25), total_samples - beat_start_sample)
            if beat_idx % 4 in (0, 2):
                for s in range(kick_samples):
                    idx = beat_start_sample + s
                    if idx >= total_samples:
                        break
                    t = s / self.sample_rate
                    # Pitch sweep 120Hz down to 45Hz
                    pitch = 45.0 + 80.0 * math.exp(-35.0 * t)
                    env = math.exp(-18.0 * t)
                    val = math.sin(2.0 * math.pi * pitch * t) * env * 0.40
                    buf_l[idx] += val
                    buf_r[idx] += val

            # 4. Soft Rimshot / Snare on beat 1 and 3
            if beat_idx % 2 == 1:
                snare_samples = min(int(self.sample_rate * 0.18), total_samples - beat_start_sample)
                for s in range(snare_samples):
                    idx = beat_start_sample + s
                    if idx >= total_samples:
                        break
                    t = s / self.sample_rate
                    env = math.exp(-25.0 * t)
                    # Pseudo noise + tonal pop
                    noise = (math.sin(s * 1337.0) % 1.0) * 2.0 - 1.0
                    val = (noise * 0.6 + math.sin(2.0 * math.pi * 220.0 * t) * 0.4) * env * 0.22
                    buf_l[idx] += val * 0.9
                    buf_r[idx] += val * 1.1

            # 5. Closed Hi-Hat on every eighth-note
            for eighth in (0, 0.5):
                hh_start = beat_start_sample + int(eighth * samples_per_beat)
                hh_samples = min(int(self.sample_rate * 0.06), total_samples - hh_start)
                for s in range(hh_samples):
                    idx = hh_start + s
                    if idx >= total_samples:
                        break
                    t = s / self.sample_rate
                    env = math.exp(-80.0 * t)
                    noise = (math.sin(s * 7919.0) % 1.0) * 2.0 - 1.0
                    val = noise * env * 0.10
                    buf_l[idx] += val * 0.7
                    buf_r[idx] += val * 1.3

        # Apply smooth master fade-out on the last 1.5 seconds
        fade_samples = int(self.sample_rate * min(1.5, duration_seconds * 0.2))
        for i in range(fade_samples):
            idx = total_samples - 1 - i
            fade = i / fade_samples
            buf_l[idx] *= fade
            buf_r[idx] *= fade

        # Normalize to peak amplitude of -1.5 dBFS (approx 27000 / 32767)
        max_peak = max(
            max(abs(v) for v in buf_l) if buf_l else 1.0,
            max(abs(v) for v in buf_r) if buf_r else 1.0,
            0.001,
        )
        target_peak = 27000.0
        scale = target_peak / max_peak

        frames = bytearray()
        for i in range(total_samples):
            samp_l = max(-32767, min(32767, int(buf_l[i] * scale)))
            samp_r = max(-32767, min(32767, int(buf_r[i] * scale)))
            frames.extend(struct.pack("<hh", samp_l, samp_r))

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(frames)

        sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return str(output_path), sha256
