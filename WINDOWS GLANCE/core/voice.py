"""
Windows Glance - voice input and output using native Windows speech.

Speech recognition and text-to-speech each run in a small PowerShell helper
(System.Speech / SAPI, built into Windows), so no extra Python packages or API
keys are needed and everything works offline.

Recognition events (one line each):
    READY | ERROR|msg | HEARD|confidence|text | HYP|partial text
    REJECT | LEVEL|0-100
Speech events:
    VOICE|name | WARN|msg | ERROR|msg | READY | DONE
"""

import os
import tempfile

from PySide6.QtCore import QObject, QProcess, QTimer, Signal


STT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$parent = __PARENT_PID__

function Send($text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}

try {
    Add-Type -AssemblyName System.Speech
    $engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $phrases = @(
__PHRASES__
    )
    $choices = New-Object System.Speech.Recognition.Choices
    $choices.Add([string[]]$phrases)
    $builder = New-Object System.Speech.Recognition.GrammarBuilder
    $builder.Append($choices)
    $engine.LoadGrammar((New-Object System.Speech.Recognition.Grammar($builder)))
    $engine.SetInputToDefaultAudioDevice()

    Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized -SourceIdentifier 'g_heard' | Out-Null
    Register-ObjectEvent -InputObject $engine -EventName SpeechHypothesized -SourceIdentifier 'g_hyp' | Out-Null
    Register-ObjectEvent -InputObject $engine -EventName SpeechRecognitionRejected -SourceIdentifier 'g_reject' | Out-Null
    Register-ObjectEvent -InputObject $engine -EventName AudioLevelUpdated -SourceIdentifier 'g_level' | Out-Null

    $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
    Send 'READY'
} catch {
    Send ('ERROR|' + $_.Exception.Message)
    exit 1
}

$lastLevel = -1
$lastSend = 0
$lastCheck = [Environment]::TickCount

while ($true) {
    if (([Environment]::TickCount - $lastCheck) -gt 2000) {
        $lastCheck = [Environment]::TickCount
        if (-not (Get-Process -Id $parent -ErrorAction SilentlyContinue)) { exit 0 }
    }

    $events = @(Wait-Event -Timeout 1)

    foreach ($ev in $events) {
        try {
            switch ($ev.SourceIdentifier) {
                'g_heard' {
                    $r = $ev.SourceEventArgs.Result
                    Send ('HEARD|' + [math]::Round($r.Confidence, 2) + '|' + $r.Text)
                }
                'g_hyp' {
                    Send ('HYP|' + $ev.SourceEventArgs.Result.Text)
                }
                'g_reject' {
                    Send 'REJECT'
                }
                'g_level' {
                    $level = [int]$ev.SourceEventArgs.AudioLevel
                    $tick = [Environment]::TickCount

                    if ((($tick - $lastSend) -ge 70) -and ($level -ne $lastLevel)) {
                        Send ('LEVEL|' + $level)
                        $lastLevel = $level
                        $lastSend = $tick
                    }
                }
            }
        } catch { }

        Remove-Event -EventIdentifier $ev.EventIdentifier -ErrorAction SilentlyContinue
    }
}
"""


TTS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'

function Send($text) {
    [Console]::Out.WriteLine($text)
    [Console]::Out.Flush()
}

$mode = 'net'
$synth = $null
$sapi = $null

try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SetOutputToDefaultAudioDevice()

    $installed = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled })
    if ($installed.Count -eq 0) { throw 'No Windows voices are installed' }

    $picked = $false

    try {
        $synth.SelectVoiceByHints(
            [System.Speech.Synthesis.VoiceGender]::Male,
            [System.Speech.Synthesis.VoiceAge]::Adult,
            0,
            (New-Object System.Globalization.CultureInfo('en-GB')))
        $picked = $true
    } catch { }

    if (-not $picked) {
        try { $synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male) } catch { }
    }

    $synth.Rate = 0
    $synth.Volume = 100
    Send ('VOICE|' + $synth.Voice.Name)
} catch {
    Send ('WARN|System.Speech unavailable (' + $_.Exception.Message + '), trying SAPI')

    try {
        $sapi = New-Object -ComObject SAPI.SpVoice
        $mode = 'sapi'
        Send ('VOICE|' + $sapi.Voice.GetDescription())
    } catch {
        Send ('ERROR|No Windows speech voice available: ' + $_.Exception.Message)
        exit 1
    }
}

Send 'READY'

while ($true) {
    $line = [Console]::In.ReadLine()

    if ($line -eq $null) { exit 0 }
    if ($line.Trim().Length -eq 0) { continue }

    try {
        if ($mode -eq 'net') { $synth.Speak($line) } else { [void]$sapi.Speak($line) }
    } catch {
        Send ('ERROR|Speech failed: ' + $_.Exception.Message)
    }

    Send 'DONE'
}
"""


class VoiceIO(QObject):
    """Owns the speech-recognition and speech-synthesis helper processes."""

    heard = Signal(str, float)     # recognised text, confidence 0..1
    hypothesis = Signal(str)       # partial text while the user is speaking
    level = Signal(int)            # microphone level 0..100
    rejected = Signal()            # speech heard but not understood
    speech_done = Signal()         # everything queued has been spoken
    status = Signal(str, bool)     # message, is_error

    def __init__(self, parent=None):
        super().__init__(parent)

        self.stt = None
        self.tts = None
        self.stt_ready = False
        self.tts_ready = False
        self.tts_failed = False
        self.speaking = False

        self._tts_path = ""
        self._pending = 0
        self._outbox = []
        self._stt_buf = ""
        self._tts_buf = ""
        self._closing = False

    # ------------------------------------------------------------------ #
    # Start and stop
    # ------------------------------------------------------------------ #

    def start(self, phrases):
        folder = tempfile.gettempdir()
        stt_path = os.path.join(folder, "windows_glance_stt.ps1")
        self._tts_path = os.path.join(folder, "windows_glance_tts.ps1")

        clean = []

        for phrase in phrases:
            text = phrase.replace("'", "")
            text = text.encode("ascii", "ignore").decode("ascii").strip()

            if text:
                clean.append(text)

        body = ",\n".join(f"        '{p}'" for p in clean)

        stt_script = (
            STT_SCRIPT
            .replace("__PARENT_PID__", str(os.getpid()))
            .replace("__PHRASES__", body)
        )

        try:
            with open(stt_path, "w", encoding="ascii") as f:
                f.write(stt_script)

            with open(self._tts_path, "w", encoding="ascii") as f:
                f.write(TTS_SCRIPT)

        except OSError as exc:
            self.tts_failed = True
            self.status.emit(
                f"Voice error: cannot write helper scripts ({exc})",
                True,
            )
            return

        self._start_tts()

        self.stt = self._launch(
            stt_path,
            self._read_stt,
            self._stt_finished,
        )

    def _start_tts(self):
        self._tts_buf = ""
        self.tts_failed = False
        self.tts_ready = False

        self.tts = self._launch(
            self._tts_path,
            self._read_tts,
            self._tts_finished,
            self._flush,
        )

    def _launch(self, path, on_output, on_finished, on_started=None):
        proc = QProcess(self)

        proc.readyReadStandardOutput.connect(on_output)
        proc.finished.connect(on_finished)
        proc.errorOccurred.connect(self._process_error)

        if on_started is not None:
            proc.started.connect(on_started)

        proc.start(
            "powershell.exe",
            [
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
        )

        return proc

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.tts_failed = True
            self.status.emit("Voice error: PowerShell could not be started", True)

    def shutdown(self):
        self._closing = True

        for proc in (self.stt, self.tts):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(500)

    # ------------------------------------------------------------------ #
    # Speech recognition
    # ------------------------------------------------------------------ #

    def _read_stt(self):
        data = bytes(self.stt.readAllStandardOutput()).decode("utf-8", "ignore")
        self._stt_buf += data

        while "\n" in self._stt_buf:
            line, self._stt_buf = self._stt_buf.split("\n", 1)
            self._stt_line(line.strip())

    def _stt_line(self, line):
        if not line:
            return

        if line.startswith("LEVEL|"):
            try:
                self.level.emit(int(line[6:]))
            except ValueError:
                pass

            return

        if line.startswith("HYP|"):
            self.hypothesis.emit(line[4:].strip())
            return

        if line == "REJECT":
            self.rejected.emit()
            return

        if line == "READY":
            self.stt_ready = True
            self.status.emit("Voice recognition ready", False)
            return

        if line.startswith("ERROR|"):
            self.status.emit("Voice error: " + line[6:], True)
            return

        if line.startswith("HEARD|"):
            parts = line.split("|", 2)

            if len(parts) == 3:
                try:
                    confidence = float(parts[1])
                except ValueError:
                    confidence = 0.0

                self.heard.emit(parts[2].strip(), confidence)

    def _stt_finished(self, _code, _status):
        if self._closing:
            return

        self.stt_ready = False
        self.status.emit("Voice error: recognition stopped", True)

    # ------------------------------------------------------------------ #
    # Speech synthesis
    # ------------------------------------------------------------------ #

    def say(self, text):
        text = str(text).encode("ascii", "ignore").decode("ascii")
        text = " ".join(text.split())

        if not text:
            QTimer.singleShot(0, self.speech_done.emit)
            return

        self.speaking = True
        self._pending += 1

        if self.tts_failed or self.tts is None:
            # No voice available: pretend to speak so the caption stays up.
            QTimer.singleShot(
                min(6000, 1500 + 60 * len(text)),
                self._finish_one,
            )
            return

        self._outbox.append(text)
        self._flush()

    def stop_speaking(self):
        """Cut off speech immediately (restarts the speech helper)."""

        self._outbox.clear()
        self._pending = 0
        self.speaking = False

        proc = self.tts

        if proc is not None:
            proc.blockSignals(True)

            if proc.state() != QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(500)

            proc.deleteLater()

        if not self._closing and self._tts_path:
            self._start_tts()

    def _flush(self):
        if self.tts is None:
            return

        if self.tts.state() != QProcess.ProcessState.Running:
            return

        while self._outbox:
            line = self._outbox.pop(0) + "\n"
            self.tts.write(line.encode("ascii", "ignore"))

    def _read_tts(self):
        data = bytes(self.tts.readAllStandardOutput()).decode("utf-8", "ignore")
        self._tts_buf += data

        while "\n" in self._tts_buf:
            line, self._tts_buf = self._tts_buf.split("\n", 1)
            line = line.strip()

            if line == "READY":
                self.tts_ready = True

            elif line == "DONE":
                self._finish_one()

            elif line.startswith("VOICE|"):
                self.status.emit("Using voice: " + line[6:], False)

            elif line.startswith("WARN|"):
                self.status.emit(line[5:], False)

            elif line.startswith("ERROR|"):
                self.status.emit("Voice error: " + line[6:], True)

    def _finish_one(self):
        self._pending = max(0, self._pending - 1)

        if self._pending == 0:
            self.speaking = False
            self.speech_done.emit()

    def _tts_finished(self, _code, _status):
        if self._closing:
            return

        self.tts_failed = True
        self.status.emit("Voice error: speech output stopped", True)

        if self._pending:
            self._pending = 0
            self.speaking = False
            self.speech_done.emit()