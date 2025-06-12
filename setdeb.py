#!/usr/bin/env python3

import sys
import os
import subprocess
import re

from PyQt5.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit,
    QMessageBox, QSizePolicy, QSpacerItem, QWidget
)
from PyQt5.QtGui import QPainter, QColor, QFont, QPen
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRectF, QSize

# --- Circular Progress Bar Widget (Diperbarui: Ukuran Diperbesar) ---
class CircularProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._minimum = 0
        self._maximum = 100
        self._progress_text = "Idle"
        # Ukuran diperbesar
        self.setMinimumSize(200, 200) 
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._progress_color = QColor(50, 150, 250)
        self._background_color = QColor(220, 220, 220)
        self._text_color = QColor(0, 0, 0)
        # Font dan ketebalan garis disesuaikan dengan ukuran baru
        self._font = QFont("Arial", 18) 
        self._pen_width = 15

    def value(self):
        return self._value

    def setValue(self, value):
        if self._value != value:
            self._value = max(self._minimum, min(self._maximum, value))
            self.update()

    def setProgressText(self, text):
        if self._progress_text != text:
            self._progress_text = text
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(self._pen_width / 2, self._pen_width / 2, -self._pen_width / 2, -self._pen_width / 2)
        span_angle = int(360 * self._value / self._maximum) if self._maximum > self._minimum else 0
        
        # Gambar latar belakang
        pen = QPen(self._background_color)
        pen.setWidth(self._pen_width)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)
        
        # Gambar progress
        pen.setColor(self._progress_color)
        painter.setPen(pen)
        painter.drawArc(rect, 90 * 16, -span_angle * 16)
        
        # Gambar teks
        painter.setPen(self._text_color)
        painter.setFont(self._font)
        text_rect = self.rect().adjusted(self._pen_width, self._pen_width, -self._pen_width, -self._pen_width)
        painter.drawText(text_rect, Qt.AlignCenter, f"{self._progress_text}\n{self._value}%")

# --- DebWorker (Tidak Berubah) ---
class DebWorker(QThread):
    packageInfoReady = pyqtSignal(dict)
    fileListReady = pyqtSignal(str)
    dependenciesReady = pyqtSignal(str)
    logMessage = pyqtSignal(str)
    analysisStatusUpdate = pyqtSignal(str)
    analysisComplete = pyqtSignal(bool)
    installationProgress = pyqtSignal(int, str)
    installationFinished = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.deb_path = None
        self._current_task = None
        self._password = None
        self.current_progress = 0

    def run_installation_command(self, command_list, password):
        full_command = ['sudo', '-S'] + command_list
        self.logMessage.emit(f"[SUDO] Running: sudo -S {' '.join(command_list)}")
        try:
            process = subprocess.Popen(full_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, errors='replace')
            if process.stdin:
                process.stdin.write(password + '\n')
                process.stdin.flush()

            packages_to_configure = []
            parsing_packages = False
            setup_counter = 0
            
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    self.logMessage.emit(f"[APT] {line.strip()}")
                    
                    if "The following NEW packages will be installed:" in line or \
                       "The following packages will be upgraded:" in line or \
                       "The following additional packages will be installed:" in line:
                        parsing_packages = True
                        continue
                    
                    if parsing_packages:
                        if line.startswith("  "):
                            packages_to_configure.extend(line.strip().split())
                        else:
                            parsing_packages = False
                            self.logMessage.emit(f"[INFO] Found {len(packages_to_configure)} packages to configure.")

                    download_match = re.search(r"Progress:.*?(\d+)%", line)
                    if download_match:
                        progress_percent = int(download_match.group(1))
                        scaled_progress = 10 + int(progress_percent * 0.65)
                        self.current_progress = scaled_progress
                        self.installationProgress.emit(self.current_progress, "Downloading...")
                        continue

                    if "Unpacking" in line or "Preparing to unpack" in line:
                        if self.current_progress < 75:
                            self.current_progress = 75
                            self.installationProgress.emit(self.current_progress, "Unpacking...")
                        continue

                    if "Setting up" in line:
                        setup_counter += 1
                        if packages_to_configure:
                            total_packages = len(packages_to_configure)
                            setup_progress = 90 + int((setup_counter / total_packages) * 8)
                            self.current_progress = setup_progress
                            self.installationProgress.emit(self.current_progress, f"Setting up ({setup_counter}/{total_packages})")
                        elif self.current_progress < 90:
                            self.current_progress = 90
                            self.installationProgress.emit(self.current_progress, "Setting up...")
                        continue
            
            return_code = process.wait()
            
            if process.stderr:
                stderr_output = process.stderr.read()
                if stderr_output:
                    if "WARNING:" in stderr_output:
                        self.logMessage.emit(f"[APT] WARNING: {stderr_output.strip()}")
                    else:
                        self.logMessage.emit(f"[APT] ERROR: {stderr_output.strip()}")
            return return_code
        except Exception as e:
            self.logMessage.emit(f"[SUDO] ERROR: Exception: {str(e)}")
            return -1
    
    def analyze_deb(self, deb_path):
        self.deb_path = deb_path
        self._current_task = "analyze"
        self.start()

    def _do_analyze_deb(self):
        self.analysisStatusUpdate.emit("Extracting package metadata...")
        info_ok, contents_ok = False, False
        fields = ['Package','Version','Architecture','Maintainer','Installed-Size','Description','Depends']
        try:
            out_info = subprocess.check_output(['dpkg-deb', '-f', self.deb_path] + fields, text=True)
            package_data = dict(re.findall(r"([A-Za-z-]+): (.*(?:\n .*)?)", out_info))
            self.packageInfoReady.emit(package_data)
            self.dependenciesReady.emit(package_data.get('Depends', 'No dependencies listed.'))
            info_ok = True
        except Exception as e:
            self.logMessage.emit(f"Error extracting package info: {e}")
        
        if info_ok:
            self.analysisStatusUpdate.emit("Listing package contents...")
            try:
                out_list = subprocess.check_output(['dpkg-deb', '-c', self.deb_path], text=True)
                self.fileListReady.emit(out_list)
                contents_ok = True
            except Exception as e:
                self.logMessage.emit(f"Error listing files: {e}")
        self.analysisComplete.emit(info_ok and contents_ok)

    def install_package(self, deb_path, password):
        self.deb_path = deb_path
        self._password = password
        self._current_task = "install"
        self.start()

    def _do_install_package(self):
        self.current_progress = 0
        self.installationProgress.emit(10, "Authenticating")
        ret = self.run_installation_command(['apt', 'install', '--yes', self.deb_path], self._password)
        self._password = None
        if ret == 0:
            self.installationProgress.emit(100, "Installed")
            self.installationFinished.emit(True, "Package installed successfully.")
        else:
            self.installationFinished.emit(False, "Installation failed. Check terminal for details.")
    
    def run(self):
        if self._current_task == "analyze":
            self._do_analyze_deb()
        elif self._current_task == "install":
            self._do_install_package()


# --- Wizard Pages ---
class AnalysisConfirmationPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Package Analysis & Confirmation")
        self.analysis_done = False
        self.analysis_successful = False
        layout = QVBoxLayout(self)
        self.lbl_pkg_name=QLabel("Name: N/A")
        self.lbl_pkg_version=QLabel("Version: N/A")
        self.txt_pkg_description=QTextEdit("Description: N/A")
        self.txt_pkg_description.setReadOnly(True)
        self.txt_dependencies=QTextEdit("Dependencies: N/A")
        self.txt_dependencies.setReadOnly(True)
        self.status_label=QLabel("Analyzing...")
        layout.addWidget(self.lbl_pkg_name)
        layout.addWidget(self.lbl_pkg_version)
        layout.addWidget(self.txt_pkg_description)
        layout.addWidget(QLabel("<b>Dependencies:</b>"))
        layout.addWidget(self.txt_dependencies)
        layout.addStretch()
        layout.addWidget(self.status_label)

    def initializePage(self):
        self.analysis_done = False
        self.analysis_successful = False
        self.completeChanged.emit()
        self.wizard().start_package_analysis(self.wizard().deb_path)

    def update_status_label(self, s):
        self.status_label.setText(s)

    def update_package_info(self, i):
        self.lbl_pkg_name.setText(f"Name: <b>{i.get('Package', 'N/A')}</b>")
        self.lbl_pkg_version.setText(f"Version: {i.get('Version', 'N/A')}")
        self.txt_pkg_description.setHtml(i.get('Description', 'N/A').replace('\n', '<br>'))

    def update_dependencies(self, d):
        self.txt_dependencies.setText(d)

    def update_file_list(self, f):
        pass

    def handle_analysis_complete(self, success):
        self.analysis_done = True
        self.analysis_successful = success
        self.update_status_label("Analysis complete. Click 'Next' to proceed." if success else "Analysis failed.")
        self.completeChanged.emit()
    
    def isComplete(self):
        return self.analysis_done and self.analysis_successful

class PasswordPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Authentication Required")
        self.setSubTitle("Please enter your password to install the package.")
        layout = QVBoxLayout(self)
        info_label = QLabel("Installation requires administrative privileges. This password will be passed to 'sudo'.")
        info_label.setWordWrap(True)
        self.password_field = QLineEdit()
        self.password_field.setEchoMode(QLineEdit.Password)
        layout.addWidget(info_label)
        layout.addWidget(QLabel("Password:"))
        layout.addWidget(self.password_field)
        layout.addStretch()
        self.registerField("password*", self.password_field)

# --- Halaman Instalasi (Diperbarui: Posisi di Tengah) ---
class InstallationPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation Progress")
        self.installation_running = False
        
        main_layout = QVBoxLayout(self)
        
        # Tambahkan stretcher di atas untuk mendorong ke tengah
        main_layout.addStretch(1)
        
        # Layout horizontal untuk progress bar agar tetap di tengah secara horizontal
        progress_layout = QHBoxLayout()
        progress_layout.addStretch(1)
        self.progress_bar = CircularProgressBar()
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addStretch(1)
        main_layout.addLayout(progress_layout)
        
        # Tambahkan stretcher di bawah
        main_layout.addStretch(1)

    def initializePage(self):
        self.installation_running = True
        self.completeChanged.emit()
        self.setFinalPage(False)
        self.update_progress(5, "Initializing...")
        password = self.field("password")
        self.wizard().start_package_installation(self.wizard().deb_path, password)
        self.setField("password", "")

    def update_progress(self, v, t):
        self.progress_bar.setValue(v)
        self.progress_bar.setProgressText(t)

    def handle_installation_finished(self, success, message):
        self.installation_running = False
        self.setFinalPage(True)
        self.wizard().installation_result_message = message
        self.wizard().installation_success_status = success
        if success:
            self.update_progress(100, "Completed!")
        else:
            self.progress_bar.setProgressText("Failed")
        self.completeChanged.emit()

    def isComplete(self):
        return not self.installation_running

class FinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation Finished")
        layout = QVBoxLayout(self)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
    
    def initializePage(self):
        msg = self.wizard().installation_result_message
        if self.wizard().installation_success_status:
            self.status_label.setText("<b>Installation successful.</b>")
        else:
            self.status_label.setText(f"<b>Installation failed.</b><br>{msg}")

# --- Main Wizard Application ---
class DebInstallerWizard(QWizard):
    Page_Analysis = 0
    Page_Password = 1
    Page_Installation = 2
    Page_Finish = 3

    def __init__(self, deb_path, parent=None):
        super().__init__(parent)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setWindowTitle(f"Installer - {os.path.basename(deb_path)}")
        
        self.deb_path = deb_path
        self.deb_worker = DebWorker()
        self.installation_result_message = ""
        self.installation_success_status = False
        
        self.setPage(self.Page_Analysis, AnalysisConfirmationPage(self))
        self.setPage(self.Page_Password, PasswordPage(self))
        self.setPage(self.Page_Installation, InstallationPage(self))
        self.setPage(self.Page_Finish, FinishPage(self))
        self.setStartId(self.Page_Analysis)

        self.deb_worker.logMessage.connect(self.print_log_to_console)
        self.deb_worker.analysisStatusUpdate.connect(self.page(self.Page_Analysis).update_status_label)
        self.deb_worker.packageInfoReady.connect(self.page(self.Page_Analysis).update_package_info)
        self.deb_worker.dependenciesReady.connect(self.page(self.Page_Analysis).update_dependencies)
        self.deb_worker.fileListReady.connect(self.page(self.Page_Analysis).update_file_list)
        self.deb_worker.analysisComplete.connect(self.page(self.Page_Analysis).handle_analysis_complete)
        self.deb_worker.installationProgress.connect(self.page(self.Page_Installation).update_progress)
        self.deb_worker.installationFinished.connect(self.page(self.Page_Installation).handle_installation_finished)

    def print_log_to_console(self, message):
        print(message, file=sys.stderr)

    def start_package_analysis(self, p):
        if self.deb_worker.isRunning(): return
        self.deb_worker.analyze_deb(p)

    def start_package_installation(self, deb_path, password):
        if self.deb_worker.isRunning(): return
        self.deb_worker.install_package(deb_path, password)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    if len(sys.argv) < 2:
        QMessageBox.critical(None, "Error", f"<b>Usage:</b> {os.path.basename(sys.argv[0])} &lt;path-to-deb-file&gt;")
        sys.exit(1)
        
    deb_file_path = os.path.abspath(sys.argv[1])
    
    if not os.path.isfile(deb_file_path) or not deb_file_path.lower().endswith('.deb'):
        QMessageBox.critical(None, "Error", f"The file '<b>{deb_file_path}</b>' is not a valid .deb file.")
        sys.exit(1)
        
    wizard = DebInstallerWizard(deb_path=deb_file_path)
    # Ukuran wizard disesuaikan agar progress bar terlihat bagus
    wizard.resize(640, 520) 
    sys.exit(wizard.exec_())