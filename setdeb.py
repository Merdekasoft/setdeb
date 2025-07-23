#!/usr/bin/env python3

import sys
import os
import subprocess
import re

from PySide6.QtWidgets import (
    QApplication, QWizard, QWizardPage, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit,
    QMessageBox, QSizePolicy, QSpacerItem, QWidget, QFormLayout, QStyle,  # <-- add this import
)
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QIcon
from PySide6.QtCore import Qt, QThread, Signal, QRectF, QSize, QTimer

# --- Circular Progress Bar Widget (Gaya diperbarui) ---
class CircularProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self._minimum = 0
        self._maximum = 100
        self._progress_text = "Idle"
        # Ukuran disesuaikan untuk tata letak yang lebih bersih
        self.setFixedSize(160, 160)
        self._progress_color = QColor("#3498db")  # Biru modern yang lebih cerah
        self._background_color = QColor("#e0e0e0") # Abu-abu yang sedikit lebih gelap
        self._text_color = QColor("#333333")      # Abu-abu gelap untuk kontras
        self._font = QFont("Segoe UI", 12)
        self._pen_width = 10 # Garis yang sedikit lebih ramping

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
        pen.setCapStyle(Qt.RoundCap) # Cap yang dibulatkan untuk tampilan yang lebih lembut
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
    packageInfoReady = Signal(dict)
    fileListReady = Signal(str)
    dependenciesReady = Signal(str)
    logMessage = Signal(str)
    analysisStatusUpdate = Signal(str)
    analysisComplete = Signal(bool)
    installationProgress = Signal(int, str)
    installationFinished = Signal(bool, str)
    packageAlreadyInstalled = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.deb_path = None
        self._current_task = None
        self._password = None
        self.current_progress = 0
        self._cleanup_needed = False

    def check_if_installed(self, package_name):
        try:
            result = subprocess.run(['dpkg', '-l', package_name],
                                 capture_output=True, text=True, check=False)
            if f"ii  {package_name}" in result.stdout:
                return True
        except FileNotFoundError:
            self.logMessage.emit("[ERROR] `dpkg` command not found. Are you on a Debian-based system?")
        except Exception as e:
            self.logMessage.emit(f"[ERROR] Failed to check if package is installed: {e}")
        return False

    def run_installation_command(self, command_list, password):
        try:
            pkg_name = subprocess.check_output(['dpkg-deb', '-f', self.deb_path, 'Package'],
                                            text=True).strip()
            if self.check_if_installed(pkg_name):
                self.packageAlreadyInstalled.emit(True, pkg_name)
                return 0
        except Exception:
            pass

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
                    line_stripped = line.strip()
                    # Filter out apt warning about unstable CLI and debconf dialog fallback
                    if (
                        "apt does not have a stable CLI interface" in line_stripped or
                        "debconf: unable to initialize frontend: Dialog" in line_stripped or
                        "debconf: (Dialog frontend requires a screen at least" in line_stripped or
                        "debconf: falling back to frontend: Readline" in line_stripped
                    ):
                        continue
                    self.logMessage.emit(f"[APT] {line_stripped}")

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
                            if packages_to_configure:
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
                        total_packages = len(packages_to_configure) if packages_to_configure else 1
                        setup_progress = 90 + int((setup_counter / total_packages) * 8 if total_packages > 0 else 8)
                        self.current_progress = min(setup_progress, 98)
                        self.installationProgress.emit(self.current_progress, f"Setting up ({setup_counter}/{total_packages})")
                        continue

            return_code = process.wait()

            if process.stderr:
                stderr_output = process.stderr.read()
                if stderr_output:
                    # Filter out apt warning about unstable CLI and debconf dialog fallback
                    filtered_lines = [
                        l for l in stderr_output.splitlines()
                        if "apt does not have a stable CLI interface" not in l
                        and "debconf: unable to initialize frontend: Dialog" not in l
                        and "debconf: (Dialog frontend requires a screen at least" not in l
                        and "debconf: falling back to frontend: Readline" not in l
                    ]
                    filtered_stderr = "\n".join(filtered_lines)
                    if "sudo: 1 incorrect password attempt" in filtered_stderr:
                        self.logMessage.emit("[SUDO] ERROR: Authentication failed. Incorrect password.")
                        return_code = -1
                    elif "WARNING:" in filtered_stderr:
                        self.logMessage.emit(f"[APT] WARNING: {filtered_stderr.strip()}")
                    elif filtered_stderr.strip():
                        self.logMessage.emit(f"[APT] ERROR: {filtered_stderr.strip()}")

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
        try:
            raw_info = subprocess.check_output(['dpkg-deb', '-f', self.deb_path], text=True)
            package_data = {}
            current_field, current_value = None, []

            for line in raw_info.split('\n'):
                if line.startswith(' ') and current_field:
                    current_value.append(line.strip())
                elif ': ' in line:
                    if current_field:
                        package_data[current_field] = ' '.join(current_value)
                    current_field, value = line.split(': ', 1)
                    current_value = [value.strip()]
            if current_field:
                package_data[current_field] = ' '.join(current_value)

            self.packageInfoReady.emit(package_data)
            self.dependenciesReady.emit(package_data.get('Depends', 'No dependencies listed.'))
            info_ok = True
        except Exception as e:
            self.logMessage.emit(f"Error extracting package info: {e}")

        if info_ok:
            self.analysisStatusUpdate.emit("Listing package contents...")
            try:
                # Ini bisa memakan waktu, cukup konfirmasi bahwa itu berhasil
                subprocess.check_output(['dpkg-deb', '-c', self.deb_path], text=True)
                self.fileListReady.emit("Contents listed successfully.")
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
        self.installationProgress.emit(10, "Authenticating...")
        # Gunakan apt untuk menangani dependensi secara otomatis
        ret = self.run_installation_command(['apt', 'install', '--yes', self.deb_path], self._password)
        self._password = None # Hapus kata sandi dari memori

        if ret == 0:
            self.installationProgress.emit(100, "Installed")
            self.installationFinished.emit(True, "Package installed successfully.")
        else:
            self.installationFinished.emit(False, "Installation failed. Check terminal output for details.")

    def run(self):
        if self._current_task == "analyze":
            self._do_analyze_deb()
        elif self._current_task == "install":
            self._do_install_package()
        self._current_task = None


# --- Halaman Wizard (Desain Ulang) ---
class AnalysisConfirmationPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Package Information")
        self.setSubTitle("Review the package description before proceeding.")
        self.analysis_done = False
        self.analysis_successful = False

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(25, 20, 25, 20)

        # Only show description
        self.lbl_pkg_description = QLabel("No description available.")
        self.lbl_pkg_description.setWordWrap(True)
        self.lbl_pkg_description.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ddd; padding: 10px; border-radius: 5px;")
        self.lbl_pkg_description.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        main_layout.addWidget(self.lbl_pkg_description, 1)

        main_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.status_label = QLabel("Analyzing, please wait...")
        self.status_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.status_label)
        
        self.setLayout(main_layout)

    def initializePage(self):
        self.analysis_done = False
        self.analysis_successful = False
        self.completeChanged.emit()
        self.wizard().start_package_analysis(self.wizard().deb_path)

    def update_status_label(self, s):
        self.status_label.setText(f"<i>{s}</i>")

    def update_package_info(self, info):
        # Set window title to "Installer - <package> <version>"
        pkg = info.get('Package', 'Unknown')
        ver = info.get('Version', '')
        title = f"Installer - {pkg} {ver}".strip()
        self.wizard().setWindowTitle(title)
        # Only show description
        description = info.get('Description', 'No description available.')
        self.lbl_pkg_description.setText(description)

    def handle_analysis_complete(self, success):
        self.analysis_done = True
        self.analysis_successful = success
        if success:
            self.update_status_label("Analysis complete. Click 'Next' to continue.")
        else:
            self.update_status_label("<b>Analysis failed.</b> Please check the file and try again.")
        self.completeChanged.emit()

    def isComplete(self):
        return self.analysis_done and self.analysis_successful

class PasswordPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Authentication Required")
        self.setSubTitle("Enter your password to grant administrative privileges for the installation.")

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(25, 20, 25, 20)
        
        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.info_label = QLabel("Please enter your password to continue:")
        self.info_label.setAlignment(Qt.AlignCenter)

        self.password_field = QLineEdit()
        self.password_field.setEchoMode(QLineEdit.Password)
        self.password_field.setMinimumWidth(250)
        
        # Tata letak horizontal untuk memusatkan field kata sandi
        h_layout = QHBoxLayout()
        h_layout.addStretch()
        h_layout.addWidget(self.password_field)
        h_layout.addStretch()

        layout.addWidget(self.info_label)
        layout.addLayout(h_layout)

        layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        self.setLayout(layout)
        
        # Daftarkan field agar wizard dapat mengakses nilainya
        self.registerField("password*", self.password_field)

    def initializePage(self):
        # Disable back navigation for this page
        wizard = self.wizard()
        if back_btn := wizard.button(QWizard.BackButton):
            back_btn.hide()
            back_btn.setEnabled(False)

    def cleanupPage(self):
        # Called when leaving page, prevent back navigation
        wizard = self.wizard()
        if back_btn := wizard.button(QWizard.BackButton):
            back_btn.hide()
            back_btn.setEnabled(False)
        super().cleanupPage()

class InstallationPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation in Progress")
        self.setSubTitle("Please wait while the package is being installed on your system.")
        self.installation_running = False

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(25, 20, 25, 20)

        self.progress_bar = CircularProgressBar()

        # Make log_output expand vertically when visible
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Monospace", 9))
        self.log_output.setStyleSheet("QTextEdit { background-color: #ffffff; color: #333; border: 1px solid #ccc; }")
        self.log_output.hide()
        self.log_output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)  # Allow vertical expansion

        layout.addStretch(1)
        layout.addWidget(self.progress_bar, 0, Qt.AlignCenter)
        layout.addWidget(self.log_output, 10)  # Give log_output more stretch factor
        layout.addStretch(1)

        self.switch_btn = QPushButton("Show Terminal Output")
        self.switch_btn.setCheckable(True)
        self.switch_btn.setStyleSheet("""
            QPushButton { 
                border: 1px solid #ccc; 
                padding: 8px 12px; 
                border-radius: 4px;
                background-color: #f0f0f0;
            }
            QPushButton:hover { background-color: #e0e0e0; }
            QPushButton:checked { background-color: #d0d0d0; }
        """)
        self.switch_btn.clicked.connect(self.toggle_view)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.switch_btn)
        layout.addLayout(btn_layout)
        self.setLayout(layout)

    def toggle_view(self):
        is_checked = self.switch_btn.isChecked()
        self.progress_bar.setVisible(not is_checked)
        self.log_output.setVisible(is_checked)
        self.switch_btn.setText("Show Progress" if is_checked else "Show Terminal Output")

    def initializePage(self):
        self.installation_running = True
        self.setFinalPage(False) # Halaman ini bukan yang terakhir sampai instalasi selesai
        self.completeChanged.emit()

        # Disable all buttons during installation
        wizard = self.wizard()
        for btn in [QWizard.NextButton, QWizard.FinishButton, QWizard.CancelButton]:
            if button := wizard.button(btn):
                button.setEnabled(False)
        
        self.switch_btn.setEnabled(False)
        
        self.update_progress(5, "Initializing...")
        self.log_output.clear()
        
        password = self.field("password")
        self.wizard().start_package_installation(self.wizard().deb_path, password)
        self.setField("password", "")

    def update_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.progress_bar.setProgressText(text)

    def handle_installation_finished(self, success, message):
        self.installation_running = False
        self.setFinalPage(True)
        self.wizard().installation_result_message = message 
        self.wizard().installation_success_status = success

        # Disable all wizard buttons immediately before moving to finish page
        wizard = self.wizard()
        for btn in [QWizard.NextButton, QWizard.FinishButton, QWizard.CancelButton]:
            if button := wizard.button(btn):
                button.setEnabled(False)
        self.switch_btn.setEnabled(False)

        if success:
            self.update_progress(100, "Completed!")
            self.setSubTitle("The installation has completed successfully.")
            # Move to finish page after a short delay (disable next immediately)
            QTimer.singleShot(1, lambda: self.wizard().setCurrentId(self.wizard().Page_Finish))
        else:
            self.progress_bar.setProgressText("Failed")
            self.setSubTitle("The installation encountered an error.")
            # Re-enable buttons only if failed
            for btn in [QWizard.NextButton, QWizard.FinishButton, QWizard.CancelButton]:
                if button := wizard.button(btn):
                    button.setEnabled(True)
            self.switch_btn.setEnabled(True)
            
        self.completeChanged.emit()

    def append_log(self, text):
        self.log_output.append(text)

    def isComplete(self):
        return not self.installation_running

class FinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Installation Finished")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(15)

        self.status_icon = QLabel()
        self.status_icon.setAlignment(Qt.AlignCenter)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px;")

        layout.addStretch(1)
        layout.addWidget(self.status_icon)
        layout.addWidget(self.status_label)
        layout.addStretch(2)
        
        self.setLayout(layout)

    def initializePage(self):
        # Use QStyle.StandardPixmap instead of QMessageBox.Information
        style = self.style()
        if self.wizard().installation_success_status:
            icon = style.standardIcon(QStyle.SP_MessageBoxInformation)
            message = f"<b>Installation Successful</b><br><br>{self.wizard().installation_result_message}"
        else:
            icon = style.standardIcon(QStyle.SP_MessageBoxCritical)
            message = f"<b>Installation Failed</b><br><br>{self.wizard().installation_result_message}"
        
        self.status_icon.setPixmap(icon.pixmap(64, 64))
        self.status_label.setText(message)

# --- Aplikasi Wizard Utama ---
class DebInstallerWizard(QWizard):
    Page_Analysis = 0
    Page_Password = 1
    Page_Installation = 2
    Page_Finish = 3

    def __init__(self, deb_path, parent=None):
        super().__init__(parent)
        self.setWizardStyle(QWizard.ModernStyle)
        self.setWindowTitle("Installer")
        # Never show back button: remove it from the wizard's button layout
        self.setButtonLayout([
            QWizard.Stretch,
            QWizard.NextButton,
            QWizard.FinishButton,
            QWizard.CancelButton
        ])
        # Remove/hide back button safely (avoid deleteLater, just hide and disable)
        back_btn = self.button(QWizard.BackButton)
        if back_btn:
            back_btn.hide()
            back_btn.setEnabled(False)
        
        self.deb_path = deb_path
        self.deb_worker = DebWorker()
        self.installation_result_message = ""
        self.installation_success_status = False

        self.setPage(self.Page_Analysis, AnalysisConfirmationPage(self))
        self.setPage(self.Page_Password, PasswordPage(self))
        self.setPage(self.Page_Installation, InstallationPage(self))
        self.setPage(self.Page_Finish, FinishPage(self))
        self.setStartId(self.Page_Analysis)

        # Hubungkan sinyal dari worker ke slot di UI
        self.deb_worker.analysisStatusUpdate.connect(self.page(self.Page_Analysis).update_status_label)
        self.deb_worker.packageInfoReady.connect(self.page(self.Page_Analysis).update_package_info)
        self.deb_worker.analysisComplete.connect(self.page(self.Page_Analysis).handle_analysis_complete)
        self.deb_worker.installationProgress.connect(self.page(self.Page_Installation).update_progress)
        self.deb_worker.installationFinished.connect(self.page(self.Page_Installation).handle_installation_finished)
        self.deb_worker.logMessage.connect(self.page(self.Page_Installation).append_log)
        self.deb_worker.packageAlreadyInstalled.connect(self.handle_existing_package)

    def start_package_analysis(self, path):
        if self.deb_worker.isRunning(): return
        self.deb_worker.analyze_deb(path)

    def start_package_installation(self, deb_path, password):
        if self.deb_worker.isRunning(): return
        self.deb_worker.install_package(deb_path, password)

    def handle_existing_package(self, installed, pkg_name):
        if installed:
            msg = f"Package '{pkg_name}' is already installed."
            QMessageBox.information(self, "Package Status", msg)
            # Atur pesan agar halaman akhir menampilkannya dan tandai sebagai berhasil
            self.installation_result_message = msg
            self.installation_success_status = True
            # Langsung ke halaman akhir
            self.setCurrentId(self.Page_Finish)

    # Override navigation methods to prevent back
    def back(self):
        pass
        
    def previousId(self):
        return -1


if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # Atur font default untuk konsistensi
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    if len(sys.argv) < 2:
        QMessageBox.critical(None, "Error", f"<b>Usage:</b> {os.path.basename(sys.argv[0])} &lt;path-to-deb-file&gt;")
        sys.exit(1)
        
    deb_file_path = os.path.abspath(sys.argv[1])
    
    if not os.path.isfile(deb_file_path) or not deb_file_path.lower().endswith('.deb'):
        QMessageBox.critical(None, "Error", f"The file '<b>{os.path.basename(deb_file_path)}</b>' is not a valid .deb file.")
        sys.exit(1)
        
    wizard = DebInstallerWizard(deb_path=deb_file_path)
    # Atur ukuran default yang lebih baik, biarkan tata letak menangani sisanya
    wizard.resize(580, 460)
    sys.exit(wizard.exec())
