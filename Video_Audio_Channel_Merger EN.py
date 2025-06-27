import sys
import os
import subprocess
import re

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QCheckBox, QGroupBox, QListWidget, QListWidgetItem,
    QHBoxLayout, QScrollArea, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, QMimeData, Qt

class FFmpegWorker(QThread):
    log_output = pyqtSignal(str)
    progress_update = pyqtSignal(int)  # Reflects current file progress in percentage
    finished_single_file = pyqtSignal(str, bool) 
    finished_all_files = pyqtSignal()

    def __init__(self, input_file, output_file, selected_channels, total_duration_sec):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.selected_channels = selected_channels
        self.total_duration_sec = total_duration_sec  # Total duration for FFmpeg progress
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        if not self.is_running:
            self.log_output.emit(f"Processing stopped: {os.path.basename(self.input_file)}")
            self.finished_single_file.emit(self.input_file, False) 
            return

        if not self.selected_channels:
            self.log_output.emit(f"No audio channels selected for '{os.path.basename(self.input_file)}'. Skipping.")
            self.finished_single_file.emit(self.input_file, False) 
            return

        num_selected_channels = len(self.selected_channels)
        corrected_ffmpeg_indices = list(range(num_selected_channels)) 
        
        audio_inputs = ''.join([f"[0:a:{i}]" for i in corrected_ffmpeg_indices])
        
        command = [
            "ffmpeg",
            "-i", self.input_file,
            "-map", "0:v",
            "-c:v", "copy",
            "-filter_complex", f"{audio_inputs}amix=inputs={num_selected_channels}:duration=longest[a]",
            "-map", "[a]",
            "-y",  # Overwrite output file if exists
            self.output_file
        ]

        self.log_output.emit(f"\n--- Starting FFmpeg process for '{os.path.basename(self.input_file)}' ---")
        self.log_output.emit(f"Output file: {os.path.basename(self.output_file)}")
        self.log_output.emit(f"Command: {' '.join(command)}")

        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # Hide window

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout
            text=True,
            startupinfo=startupinfo
        )

        success = True
        for line in process.stdout:
            if not self.is_running:
                process.terminate()
                self.log_output.emit(f"Processing stopped: {os.path.basename(self.input_file)}")
                success = False
                break
            
            self.log_output.emit(line.strip())  # Log each line

            # Parse FFmpeg progress output
            time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
            if time_match:
                time_str = time_match.group(1)
                h, m, s = map(float, time_str.split(':'))
                current_time_sec = h * 3600 + m * 60 + s

                if self.total_duration_sec > 0:
                    progress_percent = int((current_time_sec / self.total_duration_sec) * 100)
                    self.progress_update.emit(min(100, progress_percent))  # Don't exceed 100%

        process.wait()
        if process.returncode != 0 and self.is_running:
            self.log_output.emit(f"ERROR: An error occurred while processing '{os.path.basename(self.input_file)}'. Error code: {process.returncode}")
            success = False
        
        if self.is_running and success:
            self.log_output.emit(f"--- Processing completed for '{os.path.basename(self.input_file)}' ---")
            self.progress_update.emit(100)  # Set to 100% when done
        elif not self.is_running:
            self.log_output.emit(f"--- Processing for '{os.path.basename(self.input_file)}' stopped by user ---")
            self.progress_update.emit(0)  # Reset to 0 when stopped

        self.finished_single_file.emit(self.input_file, success)


class AudioMergeGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Audio Merger (Batch Processing & Channel Selection) by alfa")
        self.setGeometry(100, 100, 1000, 800)  # Increased window size
        self.setAcceptDrops(True)

        self.input_files_data = [] 
        self.current_processing_index = 0
        self.worker = None

        main_layout = QHBoxLayout() 

        # Left side: File List and Controls
        left_layout = QVBoxLayout()

        input_group = QGroupBox("Input Video(s)")
        input_layout = QVBoxLayout()
        
        input_button_layout = QHBoxLayout()
        self.btn_select_files = QPushButton("Select File(s)")
        self.btn_select_files.clicked.connect(self.select_input_files)
        input_button_layout.addWidget(self.btn_select_files)
        
        self.btn_clear_files = QPushButton("Clear List")
        self.btn_clear_files.clicked.connect(self.clear_file_list)
        input_button_layout.addWidget(self.btn_clear_files)
        
        input_layout.addLayout(input_button_layout)

        self.file_list_widget = QListWidget()
        self.file_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.file_list_widget.itemSelectionChanged.connect(self.on_file_selected)
        input_layout.addWidget(self.file_list_widget)
        
        input_layout.addWidget(QLabel("You can drag and drop files here."))
        input_group.setLayout(input_layout)
        left_layout.addWidget(input_group)

        # Output Directory Section
        output_group = QGroupBox("Output Directory")
        output_layout = QVBoxLayout()
        self.label_output_dir = QLabel("Output Directory: Not specified")
        self.btn_output_dir = QPushButton("Set Output Directory")
        self.btn_output_dir.clicked.connect(self.select_output_directory)
        output_layout.addWidget(self.label_output_dir)
        output_layout.addWidget(self.btn_output_dir)
        output_group.setLayout(output_layout)
        left_layout.addWidget(output_group)

        # Progress Bars
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()

        self.label_current_file_progress = QLabel("Current File Progress:")
        progress_layout.addWidget(self.label_current_file_progress)
        self.current_file_progressbar = QProgressBar()
        self.current_file_progressbar.setTextVisible(True)
        progress_layout.addWidget(self.current_file_progressbar)

        self.label_total_progress = QLabel("Total Processing Progress:")
        progress_layout.addWidget(self.label_total_progress)
        self.total_progressbar = QProgressBar()
        self.total_progressbar.setTextVisible(True)
        progress_layout.addWidget(self.total_progressbar)
        
        progress_group.setLayout(progress_layout)
        left_layout.addWidget(progress_group)


        # Process Buttons
        process_button_layout = QHBoxLayout()
        self.btn_run = QPushButton("Process All")
        self.btn_run.clicked.connect(self.start_batch_processing)
        process_button_layout.addWidget(self.btn_run)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop_processing)
        self.btn_stop.setEnabled(False) 
        process_button_layout.addWidget(self.btn_stop)
        
        left_layout.addLayout(process_button_layout)
        
        # Console Output
        self.output_log = QTextEdit()
        self.output_log.setReadOnly(True)
        left_layout.addWidget(QLabel("Console Output:"))
        left_layout.addWidget(self.output_log)

        main_layout.addLayout(left_layout, 2) 

        # Right side: Audio Channel Selection Area
        right_layout = QVBoxLayout()
        self.channel_selection_group = QGroupBox("Selected File's Audio Channels")
        self.channel_checkbox_layout = QVBoxLayout()
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        channel_container = QWidget()
        channel_container.setLayout(self.channel_checkbox_layout)
        scroll_area.setWidget(channel_container)

        # Define label_selected_file_name here
        self.label_selected_file_name = QLabel("No File Selected") 

        self.channel_selection_group.setLayout(QVBoxLayout())
        self.channel_selection_group.layout().insertWidget(0, QLabel(" ")) 
        self.channel_selection_group.layout().insertWidget(0, self.label_selected_file_name)
        self.channel_selection_group.layout().addWidget(scroll_area)
        
        right_layout.addWidget(self.channel_selection_group)
        main_layout.addLayout(right_layout, 1)


        self.setLayout(main_layout)
        
        self.output_directory = ""

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path) and (file_path.lower().endswith(('.mp4', '.mkv', '.mov', '.avi'))):
                self.add_file_to_list(file_path)
            else:
                self.output_log.append(f"Invalid file dragged: {os.path.basename(file_path) if os.path.isfile(file_path) else file_path}")
        event.acceptProposedAction()

    def select_input_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Input Video(s)", "", "Video Files (*.mp4 *.mkv *.mov *.avi)")
        if files:
            for file_path in files:
                self.add_file_to_list(file_path)

    def add_file_to_list(self, file_path):
        if any(data['path'] == file_path for data in self.input_files_data):
            return

        duration_sec = self.detect_video_duration(file_path)
        duration_str = self.format_duration(duration_sec)
        
        all_channels = self.detect_audio_channels(file_path)
        initial_selected_channels = list(all_channels)  # Initially all selected
        
        file_data = {
            'path': file_path,
            'duration_sec': duration_sec,  # Add duration info
            'all_channels': all_channels,
            'selected_channels': initial_selected_channels, 
            'checkboxes': [] 
        }
        self.input_files_data.append(file_data)
        item = QListWidgetItem(f"{os.path.basename(file_path)} ({duration_str})")  # Add duration to name
        item.setData(Qt.UserRole, len(self.input_files_data) - 1)
        self.file_list_widget.addItem(item)
        self.output_log.append(f"'{os.path.basename(file_path)}' added. Duration: {duration_str}, Detected channels: {all_channels}")

    def clear_file_list(self):
        self.input_files_data.clear()
        self.file_list_widget.clear()
        self.clear_channel_checkboxes() 
        self.label_selected_file_name.setText("No File Selected")
        self.output_log.append("File list cleared.")
        self.current_file_progressbar.setValue(0)
        self.total_progressbar.setValue(0)

    def detect_video_duration(self, file_path):
        """Detects video duration in seconds using FFprobe."""
        command = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # Hide CMD window

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # To capture errors
                text=True,
                startupinfo=startupinfo
            )
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                duration_str = stdout.strip()
                if duration_str:
                    return float(duration_str)
            else:
                self.output_log.append(f"ERROR: FFprobe error while detecting duration for '{os.path.basename(file_path)}': {stderr.strip()}")
        except Exception as e:
            self.output_log.append(f"ERROR: Exception while detecting duration for '{os.path.basename(file_path)}': {e}")
        return 0.0

    def format_duration(self, seconds):
        """Converts seconds to HH:MM:SS format."""
        if seconds is None or seconds < 0:
            return "N/A"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02}:{minutes:02}:{secs:02}"


    def detect_audio_channels(self, file_path):
        command = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            file_path
        ]

        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # Hide CMD window

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # To capture errors
                text=True,
                startupinfo=startupinfo
            )
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                channels = stdout.strip().split('\n')
            else:
                self.output_log.append(f"ERROR: FFprobe error while detecting audio channels for '{os.path.basename(file_path)}': {stderr.strip()}")
                channels = []  # Return empty list on error
        except Exception as e:
            self.output_log.append(f"ERROR: Exception while detecting audio channels for '{os.path.basename(file_path)}': {e}")
            channels = []

        detected_indices = []
        for ch in channels:
            try:
                if ch.strip(): 
                    idx = int(ch)
                    detected_indices.append(idx)
            except ValueError:
                self.output_log.append(f"Invalid channel value detected from FFprobe: '{ch}'")
                continue
        detected_indices.sort()
        return detected_indices

    def on_file_selected(self):
        selected_items = self.file_list_widget.selectedItems()
        if not selected_items:
            self.clear_channel_checkboxes()
            self.label_selected_file_name.setText("No File Selected")
            return

        item = selected_items[0]
        file_index = item.data(Qt.UserRole)
        current_file_data = self.input_files_data[file_index]
        
        self.label_selected_file_name.setText(f"Selected File: {os.path.basename(current_file_data['path'])}")
        
        self.clear_channel_checkboxes()

        current_file_data['checkboxes'] = [] 
        for idx in current_file_data['all_channels']:
            checkbox = QCheckBox(f"Audio Channel {idx}")
            checkbox.setChecked(idx in current_file_data['selected_channels'])
            
            checkbox.stateChanged.connect(lambda state, i=idx, f_idx=file_index: self.update_channel_selection(f_idx, i, state == Qt.Checked))
            
            self.channel_checkbox_layout.addWidget(checkbox)
            current_file_data['checkboxes'].append(checkbox)

    def clear_channel_checkboxes(self):
        while self.channel_checkbox_layout.count():
            item = self.channel_checkbox_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def update_channel_selection(self, file_index, channel_idx, is_checked):
        file_data = self.input_files_data[file_index]
        if is_checked:
            if channel_idx not in file_data['selected_channels']:
                file_data['selected_channels'].append(channel_idx)
                file_data['selected_channels'].sort() 
        else:
            if channel_idx in file_data['selected_channels']:
                file_data['selected_channels'].remove(channel_idx)
                file_data['selected_channels'].sort() 


    def select_output_directory(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if dir_path:
            self.output_directory = dir_path
            self.label_output_dir.setText(f"Output Directory: {dir_path}")

    def start_batch_processing(self):
        if not self.input_files_data:
            self.output_log.append("Please select at least one file to process.")
            return
        if not self.output_directory:
            self.output_log.append("Please specify the output directory.")
            return

        for file_data in self.input_files_data:
            if not file_data['selected_channels']:
                self.output_log.append(f"ERROR: No audio channels selected for '{os.path.basename(file_data['path'])}'. Please select at least one channel or remove the file from the list.")
                return

        self.output_log.clear()
        self.output_log.append("Starting batch processing...")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.current_processing_index = 0
        self.current_file_progressbar.setValue(0)
        self.total_progressbar.setValue(0)
        self.update_total_progress()  # Set initial total progress
        self.process_next_file()

    def process_next_file(self):
        if self.current_processing_index < len(self.input_files_data):
            file_data = self.input_files_data[self.current_processing_index]
            input_file = file_data['path']
            selected_channels = file_data['selected_channels']
            total_duration_sec = file_data['duration_sec']  # Get duration info

            base_name = os.path.basename(input_file)
            name_without_ext, ext = os.path.splitext(base_name)
            output_file = os.path.join(self.output_directory, f"{name_without_ext}_merged.mkv")
            
            # Highlight the file in the list
            for i in range(self.file_list_widget.count()):
                item = self.file_list_widget.item(i)
                item_file_index = item.data(Qt.UserRole)
                if item_file_index == self.current_processing_index:
                    item.setBackground(Qt.yellow)
                else:
                    item.setBackground(Qt.white)

            self.worker = FFmpegWorker(input_file, output_file, selected_channels, total_duration_sec)
            self.worker.log_output.connect(self.output_log.append)
            self.worker.progress_update.connect(self.update_current_file_progress)  # Connect new signal
            self.worker.finished_single_file.connect(self.on_single_file_finished)
            self.worker.start()
        else:
            self.output_log.append("\nAll files processed successfully!")
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.worker = None
            self.total_progressbar.setValue(100)  # Set to 100% when all done
            # Reset background for all files
            for i in range(self.file_list_widget.count()):
                self.file_list_widget.item(i).setBackground(Qt.white)

    def update_current_file_progress(self, percent):
        self.current_file_progressbar.setValue(percent)
        self.update_total_progress()  # Update total progress when current file progress changes
        
    def update_total_progress(self):
        if not self.input_files_data:
            self.total_progressbar.setValue(0)
            return
        
        total_files = len(self.input_files_data)
        completed_files = self.current_processing_index
        
        if total_files > 0:
            current_file_progress_weight = self.current_file_progressbar.value() / 100.0 if self.worker and self.worker.isRunning() else 0
            
            total_progress_value = int(((completed_files + current_file_progress_weight) / total_files) * 100)
            self.total_progressbar.setValue(total_progress_value)
        else:
            self.total_progressbar.setValue(0)


    def on_single_file_finished(self, input_file_processed, success):
        # Update the list item for the processed file
        for i in range(self.file_list_widget.count()):
            item = self.file_list_widget.item(i)
            file_data_index = item.data(Qt.UserRole)
            file_data = self.input_files_data[file_data_index]
            if file_data['path'] == input_file_processed:
                item.setBackground(Qt.white)
                if success:
                    item.setForeground(Qt.darkGreen)
                else:
                    item.setForeground(Qt.red)
                break
        
        self.current_processing_index += 1
        self.current_file_progressbar.setValue(0)  # Reset progress bar before next file
        self.update_total_progress()  # Update total progress
        self.process_next_file()

    def stop_processing(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False) 
        else:
            self.output_log.append("No active process to stop.")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = AudioMergeGUI()
    gui.show()
    sys.exit(app.exec_())