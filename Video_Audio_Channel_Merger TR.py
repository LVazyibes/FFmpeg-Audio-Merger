import sys
import os
import subprocess
import re # Düzenli ifadeler için

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
    QFileDialog, QTextEdit, QCheckBox, QGroupBox, QListWidget, QListWidgetItem,
    QHBoxLayout, QScrollArea, QProgressBar
)
from PyQt5.QtCore import QThread, pyqtSignal, QMimeData, Qt

class FFmpegWorker(QThread):
    log_output = pyqtSignal(str)
    progress_update = pyqtSignal(int) # Mevcut dosyanın ilerlemesini % olarak yansıtır
    finished_single_file = pyqtSignal(str, bool) 
    finished_all_files = pyqtSignal()

    def __init__(self, input_file, output_file, selected_channels, total_duration_sec):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.selected_channels = selected_channels
        self.total_duration_sec = total_duration_sec # FFmpeg progress için toplam süre
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        if not self.is_running:
            self.log_output.emit(f"İşlem durduruldu: {os.path.basename(self.input_file)}")
            self.finished_single_file.emit(self.input_file, False) 
            return

        if not self.selected_channels:
            self.log_output.emit(f"'{os.path.basename(self.input_file)}' için hiçbir ses kanalı seçilmedi. Atlanıyor.")
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
            "-y", # Çıkış dosyası varsa üzerine yaz
            self.output_file
        ]

        self.log_output.emit(f"\n--- '{os.path.basename(self.input_file)}' için FFmpeg işlemi başlatılıyor ---")
        self.log_output.emit(f"Çıkış dosyası: {os.path.basename(self.output_file)}")
        self.log_output.emit(f"Komut: {' '.join(command)}")

        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW # Pencereyi gizle

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # stderr'i de stdout'a yönlendir
            text=True,
            startupinfo=startupinfo
        )

        success = True
        for line in process.stdout:
            if not self.is_running:
                process.terminate()
                self.log_output.emit(f"İşlem durduruldu: {os.path.basename(self.input_file)}")
                success = False
                break
            
            self.log_output.emit(line.strip()) # Her satırı logla

            # FFmpeg ilerleme çıktısını parse et
            time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
            if time_match:
                time_str = time_match.group(1)
                h, m, s = map(float, time_str.split(':'))
                current_time_sec = h * 3600 + m * 60 + s

                if self.total_duration_sec > 0:
                    progress_percent = int((current_time_sec / self.total_duration_sec) * 100)
                    self.progress_update.emit(min(100, progress_percent)) # %100'ü geçmesin

        process.wait()
        if process.returncode != 0 and self.is_running:
            self.log_output.emit(f"HATA: '{os.path.basename(self.input_file)}' işlemi sırasında bir hata oluştu. Hata kodu: {process.returncode}")
            success = False
        
        if self.is_running and success:
            self.log_output.emit(f"--- '{os.path.basename(self.input_file)}' işlemi tamamlandı ---")
            self.progress_update.emit(100) # İşlem bitince %100'e set et
        elif not self.is_running:
            self.log_output.emit(f"--- '{os.path.basename(self.input_file)}' işlemi kullanıcı tarafından durduruldu ---")
            self.progress_update.emit(0) # Durdurulduysa sıfırla veya isteğe bağlı olarak son bilinen %de bırak

        self.finished_single_file.emit(self.input_file, success)


class AudioMergeGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FFmpeg Ses Birleştirici (Toplu İşlem & Kanal Seçimi) by alfa")
        self.setGeometry(100, 100, 1000, 800) # Pencere boyutunu büyüttük
        self.setAcceptDrops(True)

        self.input_files_data = [] 
        self.current_processing_index = 0
        self.worker = None

        main_layout = QHBoxLayout() 

        # Sol taraf: Dosya Listesi ve Kontroller
        left_layout = QVBoxLayout()

        input_group = QGroupBox("Giriş Video(ları)")
        input_layout = QVBoxLayout()
        
        input_button_layout = QHBoxLayout()
        self.btn_select_files = QPushButton("Dosya(ları) Seç")
        self.btn_select_files.clicked.connect(self.select_input_files)
        input_button_layout.addWidget(self.btn_select_files)
        
        self.btn_clear_files = QPushButton("Listeyi Temizle")
        self.btn_clear_files.clicked.connect(self.clear_file_list)
        input_button_layout.addWidget(self.btn_clear_files)
        
        input_layout.addLayout(input_button_layout)

        self.file_list_widget = QListWidget()
        self.file_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.file_list_widget.itemSelectionChanged.connect(self.on_file_selected)
        input_layout.addWidget(self.file_list_widget)
        
        input_layout.addWidget(QLabel("Dosyaları buraya sürükleyip bırakabilirsiniz."))
        input_group.setLayout(input_layout)
        left_layout.addWidget(input_group)

        # Çıkış Dizini Bölümü
        output_group = QGroupBox("Çıkış Dizini")
        output_layout = QVBoxLayout()
        self.label_output_dir = QLabel("Çıkış Dizini: Henüz belirlenmedi")
        self.btn_output_dir = QPushButton("Çıkış Dizini Belirle")
        self.btn_output_dir.clicked.connect(self.select_output_directory)
        output_layout.addWidget(self.label_output_dir)
        output_layout.addWidget(self.btn_output_dir)
        output_group.setLayout(output_layout)
        left_layout.addWidget(output_group)

        # İlerleme Çubukları
        progress_group = QGroupBox("İlerleme")
        progress_layout = QVBoxLayout()

        self.label_current_file_progress = QLabel("Mevcut Dosya İlerlemesi:")
        progress_layout.addWidget(self.label_current_file_progress)
        self.current_file_progressbar = QProgressBar()
        self.current_file_progressbar.setTextVisible(True)
        progress_layout.addWidget(self.current_file_progressbar)

        self.label_total_progress = QLabel("Toplam İşlem İlerlemesi:")
        progress_layout.addWidget(self.label_total_progress)
        self.total_progressbar = QProgressBar()
        self.total_progressbar.setTextVisible(True)
        progress_layout.addWidget(self.total_progressbar)
        
        progress_group.setLayout(progress_layout)
        left_layout.addWidget(progress_group)


        # İşlem Butonları
        process_button_layout = QHBoxLayout()
        self.btn_run = QPushButton("Tümünü İşle")
        self.btn_run.clicked.connect(self.start_batch_processing)
        process_button_layout.addWidget(self.btn_run)

        self.btn_stop = QPushButton("Durdur")
        self.btn_stop.clicked.connect(self.stop_processing)
        self.btn_stop.setEnabled(False) 
        process_button_layout.addWidget(self.btn_stop)
        
        left_layout.addLayout(process_button_layout)
        
        # Konsol Çıkışı
        self.output_log = QTextEdit()
        self.output_log.setReadOnly(True)
        left_layout.addWidget(QLabel("Konsol Çıkıtısı:"))
        left_layout.addWidget(self.output_log)

        main_layout.addLayout(left_layout, 2) 

        # Sağ taraf: Ses Kanalı Seçim Alanı
        right_layout = QVBoxLayout()
        self.channel_selection_group = QGroupBox("Seçilen Dosyanın Ses Kanalları")
        self.channel_checkbox_layout = QVBoxLayout()
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        channel_container = QWidget()
        channel_container.setLayout(self.channel_checkbox_layout)
        scroll_area.setWidget(channel_container)

        # label_selected_file_name'i burada tanımlıyoruz
        self.label_selected_file_name = QLabel("Dosya Seçilmedi") 

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
                self.output_log.append(f"Geçersiz dosya sürükle-bırakıldı: {os.path.basename(file_path) if os.path.isfile(file_path) else file_path}")
        event.acceptProposedAction()

    def select_input_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Giriş Video(ları) Seç", "", "Video Dosyaları (*.mp4 *.mkv *.mov *.avi)")
        if files:
            for file_path in files:
                self.add_file_to_list(file_path)

    def add_file_to_list(self, file_path):
        if any(data['path'] == file_path for data in self.input_files_data):
            return

        duration_sec = self.detect_video_duration(file_path)
        duration_str = self.format_duration(duration_sec)
        
        all_channels = self.detect_audio_channels(file_path)
        initial_selected_channels = list(all_channels) # Başlangıçta tümü seçili
        
        file_data = {
            'path': file_path,
            'duration_sec': duration_sec, # Süre bilgisini ekle
            'all_channels': all_channels,
            'selected_channels': initial_selected_channels, 
            'checkboxes': [] 
        }
        self.input_files_data.append(file_data)
        item = QListWidgetItem(f"{os.path.basename(file_path)} ({duration_str})") # Süreyi isme ekle
        item.setData(Qt.UserRole, len(self.input_files_data) - 1)
        self.file_list_widget.addItem(item)
        self.output_log.append(f"'{os.path.basename(file_path)}' eklendi. Süre: {duration_str}, Algılanan kanallar: {all_channels}")

    def clear_file_list(self):
        self.input_files_data.clear()
        self.file_list_widget.clear()
        self.clear_channel_checkboxes() 
        self.label_selected_file_name.setText("Dosya Seçilmedi")
        self.output_log.append("Dosya listesi temizlendi.")
        self.current_file_progressbar.setValue(0)
        self.total_progressbar.setValue(0)

    def detect_video_duration(self, file_path):
        """FFprobe kullanarak videonun süresini saniye cinsinden algılar."""
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
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW # CMD penceresini gizle

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, # Hataları da yakalamak için
                text=True,
                startupinfo=startupinfo
            )
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                duration_str = stdout.strip()
                if duration_str:
                    return float(duration_str)
            else:
                self.output_log.append(f"HATA: '{os.path.basename(file_path)}' süresi algılanırken ffprobe hatası: {stderr.strip()}")
        except Exception as e:
            self.output_log.append(f"HATA: '{os.path.basename(file_path)}' süresi algılanırken istisna: {e}")
        return 0.0

    def format_duration(self, seconds):
        """Saniyeyi HH:MM:SS formatına dönüştürür."""
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
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW # CMD penceresini gizle

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, # Hataları da yakalamak için
                text=True,
                startupinfo=startupinfo
            )
            stdout, stderr = process.communicate()
            if process.returncode == 0:
                channels = stdout.strip().split('\n')
            else:
                self.output_log.append(f"HATA: '{os.path.basename(file_path)}' ses kanalları algılanırken ffprobe hatası: {stderr.strip()}")
                channels = [] # Hata durumunda boş liste döndür
        except Exception as e:
            self.output_log.append(f"HATA: '{os.path.basename(file_path)}' ses kanalları algılanırken istisna: {e}")
            channels = []

        detected_indices = []
        for ch in channels:
            try:
                if ch.strip(): 
                    idx = int(ch)
                    detected_indices.append(idx)
            except ValueError:
                self.output_log.append(f"FFprobe'dan geçersiz kanal değeri algılandı: '{ch}'")
                continue
        detected_indices.sort()
        return detected_indices

    def on_file_selected(self):
        selected_items = self.file_list_widget.selectedItems()
        if not selected_items:
            self.clear_channel_checkboxes()
            self.label_selected_file_name.setText("Dosya Seçilmedi")
            return

        item = selected_items[0]
        file_index = item.data(Qt.UserRole)
        current_file_data = self.input_files_data[file_index]
        
        self.label_selected_file_name.setText(f"Seçilen Dosya: {os.path.basename(current_file_data['path'])}")
        
        self.clear_channel_checkboxes()

        current_file_data['checkboxes'] = [] 
        for idx in current_file_data['all_channels']:
            checkbox = QCheckBox(f"Ses Kanalı {idx}")
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
        dir_path = QFileDialog.getExistingDirectory(self, "Çıkış Dizini Seç")
        if dir_path:
            self.output_directory = dir_path
            self.label_output_dir.setText(f"Çıkış Dizini: {dir_path}")

    def start_batch_processing(self):
        if not self.input_files_data:
            self.output_log.append("Lütfen işlemek için en az bir dosya seçin veya sürükleyin.")
            return
        if not self.output_directory:
            self.output_log.append("Lütfen çıkış dizinini belirleyin.")
            return

        for file_data in self.input_files_data:
            if not file_data['selected_channels']:
                self.output_log.append(f"HATA: '{os.path.basename(file_data['path'])}' için hiçbir ses kanalı seçilmedi. Lütfen en az bir kanal seçin veya dosyayı listeden çıkarın.")
                return

        self.output_log.clear()
        self.output_log.append("Toplu işlem başlatılıyor...")
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.current_processing_index = 0
        self.current_file_progressbar.setValue(0)
        self.total_progressbar.setValue(0)
        self.update_total_progress() # Başlangıç toplam ilerlemeyi ayarla
        self.process_next_file()

    def process_next_file(self):
        if self.current_processing_index < len(self.input_files_data):
            file_data = self.input_files_data[self.current_processing_index]
            input_file = file_data['path']
            selected_channels = file_data['selected_channels']
            total_duration_sec = file_data['duration_sec'] # Süre bilgisini al

            base_name = os.path.basename(input_file)
            name_without_ext, ext = os.path.splitext(base_name)
            output_file = os.path.join(self.output_directory, f"{name_without_ext}_merged.mkv")
            
            # Dosya listede highlight edilsin
            for i in range(self.file_list_widget.count()):
                item = self.file_list_widget.item(i)
                item_file_index = item.data(Qt.UserRole)
                if item_file_index == self.current_processing_index:
                    item.setBackground(Qt.yellow)
                else:
                    item.setBackground(Qt.white)

            self.worker = FFmpegWorker(input_file, output_file, selected_channels, total_duration_sec)
            self.worker.log_output.connect(self.output_log.append)
            self.worker.progress_update.connect(self.update_current_file_progress) # Yeni sinyali bağla
            self.worker.finished_single_file.connect(self.on_single_file_finished)
            self.worker.start()
        else:
            self.output_log.append("\nTüm dosyalar başarıyla işlendi!")
            self.btn_run.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.worker = None
            self.total_progressbar.setValue(100) # Tüm işlem bitince %100 yap
            # Tüm dosyaların arka planını sıfırla
            for i in range(self.file_list_widget.count()):
                self.file_list_widget.item(i).setBackground(Qt.white)

    def update_current_file_progress(self, percent):
        self.current_file_progressbar.setValue(percent)
        self.update_total_progress() # Mevcut dosyanın ilerlemesi değiştiğinde toplam ilerlemeyi de güncelle
        
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
        # İşlem tamamlanan dosyanın listedeki öğesini güncelle
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
        self.current_file_progressbar.setValue(0) # Yeni dosyaya geçmeden önceki çubuğu sıfırla
        self.update_total_progress() # Toplam ilerlemeyi güncelle
        self.process_next_file()

    def stop_processing(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False) 
        else:
            self.output_log.append("Durdurulacak aktif bir işlem yok.")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = AudioMergeGUI()
    gui.show()
    sys.exit(app.exec_())
