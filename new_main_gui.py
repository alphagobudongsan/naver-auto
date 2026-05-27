import sys
import os
import json
import asyncio
import traceback
import subprocess
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                               QTableWidget, QTableWidgetItem, QHeaderView, 
                               QTextEdit, QGroupBox, QCheckBox, QMessageBox)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont

# 전역 예외 처리기 등록
def exception_hook(exctype, value, tb):
    print("Unhandled Exception Details:", file=sys.stderr)
    traceback.print_exception(exctype, value, tb, file=sys.stderr)
    try:
        with open("crash_log.txt", "w", encoding="utf-8") as f:
            traceback.print_exception(exctype, value, tb, file=f)
    except Exception:
        pass
    sys.exit(1)

sys.excepthook = exception_hook

# 인코딩 강제 설정 (Windows 콘솔 출력 예외 방지)
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# 모듈 로드 경로 강제 설정 (격리 모드 대비)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    import github_automation
except ImportError:
    github_automation = None

CONFIG_FILE = "config_template.json"


class RankCheckThread(QThread):
    log_signal = Signal(str)
    result_signal = Signal(list)
    finished_signal = Signal()

    def __init__(self, agent_id, parent=None):
        super().__init__(parent)
        self.agent_id = agent_id

    def run(self):
        try:
            import logic_handler

            def log_callback(msg):
                self.log_signal.emit(msg)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            results = loop.run_until_complete(
                logic_handler.run_real_logic(self.agent_id, log_callback, test_mode=False)
            )
            loop.close()

            formatted_data = []
            if results:
                for item in results:
                    price_change = item.get("price_change", "-")
                    if "error" in item:
                        price_change = "에러"

                    formatted_data.append({
                        "id": item.get("article_no", item.get("atclNo", "")),
                        "name": item.get("name", item.get("atclNm", "")),
                        "type": item.get("property_type", item.get("rletTpNm", "")),
                        "price": item.get("price", item.get("prc", "")),
                        "date": item.get("reg_date", item.get("atclCfmYmd", "")),
                        "rank": item.get("rank", "-"),
                        "update": item.get("update_needed", False),
                        "floor_open": "O" if item.get("floor_disclosed") else "X",
                        "price_change": price_change
                    })
            else:
                self.log_signal.emit("조회된 매물이 없거나 오류가 발생했습니다.")
            
            self.result_signal.emit(formatted_data)
        except Exception as e:
            self.log_signal.emit(f"실행 중 오류 발생: {e}")
        finally:
            self.finished_signal.emit()


class GitHubPushThread(QThread):
    log_signal = Signal(str)
    result_signal = Signal(str, bool)

    def __init__(self, action, items, parent=None):
        super().__init__(parent)
        self.action = action
        self.items = items

    def run(self):
        if not github_automation:
            self.log_signal.emit("오류: github_automation 모듈을 불러올 수 없습니다.")
            self.result_signal.emit(self.action, False)
            return

        try:
            self.log_signal.emit("GitHub 연동을 준비 중입니다...")
            gh = github_automation.GitHubAutomation(config_path=CONFIG_FILE)

            if self.action == "schedule":
                self.log_signal.emit(f"선택된 {len(self.items)}개의 매물을 매일 자동 실행하도록 예약합니다...")
                gh.create_scheduled_file(self.items)
                gh.push_to_github(self.items)

                # ★ data/scheduled_properties.json을 GitHub에 push (Actions push 트리거용)
                self.log_signal.emit("[INFO] 예약 파일을 GitHub에 업로드 중...")
                try:
                    scheduled_file = os.path.join("data", "scheduled_properties.json")
                    if os.path.exists(scheduled_file):
                        # git add
                        r = subprocess.run(
                            ["git", "add", scheduled_file],
                            capture_output=True, text=True, encoding="utf-8", errors="replace"
                        )
                        if r.returncode != 0:
                            self.log_signal.emit(f"[WARN] git add 실패: {r.stderr.strip()}")
                        else:
                            # git commit
                            from datetime import datetime
                            commit_msg = f"Scheduled {len(self.items)} properties ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
                            r2 = subprocess.run(
                                ["git", "commit", "-m", commit_msg],
                                capture_output=True, text=True, encoding="utf-8", errors="replace"
                            )
                            if r2.returncode != 0 and "nothing to commit" not in r2.stdout:
                                self.log_signal.emit(f"[WARN] git commit 실패: {r2.stderr.strip()}")
                            else:
                                # git push
                                r3 = subprocess.run(
                                    ["git", "push"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace"
                                )
                                if r3.returncode == 0:
                                    self.log_signal.emit("[OK] 예약 파일 GitHub 업로드 완료 → 자동화 워크플로우가 시작됩니다!")
                                else:
                                    self.log_signal.emit(f"[WARN] git push 실패: {r3.stderr.strip()}")
                    else:
                        self.log_signal.emit(f"[WARN] 예약 파일을 찾을 수 없습니다: {scheduled_file}")
                except Exception as e2:
                    self.log_signal.emit(f"[WARN] 예약 파일 push 중 오류: {e2}")

                self.log_signal.emit("자동화 예약 설정이 클라우드 서버에 안전하게 등록되었습니다!")

            elif self.action == "cancel":
                self.log_signal.emit("자동화 예약을 취소합니다...")
                gh.cancel_schedule()
                self.log_signal.emit("자동화 예약이 성공적으로 취소되었습니다.")

            self.result_signal.emit(self.action, True)
        except Exception as e:
            self.log_signal.emit(f"GitHub 연동 중 오류 발생: {e}")
            self.result_signal.emit(self.action, False)


class AdUpdateThread(QThread):
    log_signal = Signal(str)
    result_signal = Signal(bool)

    def __init__(self, items, config, parent=None):
        super().__init__(parent)
        self.items = items
        self.config = config

    def run(self):
        try:
            self.log_signal.emit("로컬 자동화 브라우저(Playwright)를 즉시 시작합니다...")
            env = os.environ.copy()
            env["LOGIN_ID"] = self.config.get("ai_partner_login_id", "")
            env["LOGIN_PASSWORD"] = self.config.get("ai_partner_password", "")
            env["PROPERTY_NUMBERS"] = ",".join(self.items)
            env["TEST_MODE"] = "false"
            env["PYTHONIOENCODING"] = "utf-8"

            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                ["python", "multi_property_automation.py"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags
            )

            for line in process.stdout:
                if line.strip():
                    self.log_signal.emit(line.strip())

            process.wait()
            if process.returncode == 0:
                self.log_signal.emit("업데이트 즉시실행이 성공적으로 완료되었습니다.")
                self.result_signal.emit(True)
            else:
                self.log_signal.emit(f"업데이트 즉시실행 중 오류가 발생했습니다. (코드: {process.returncode})")
                self.result_signal.emit(False)
        except Exception as e:
            self.log_signal.emit(f"자동화 프로세스 실행 실패: {e}")
            self.result_signal.emit(False)


class NewMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("네이버 부동산 순위 조회 (금액변경필요 기능 포함)")
        self.resize(1200, 700)
        self.load_config()
        self.init_ui()

    def load_config(self):
        self.config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            except Exception:
                pass

    def save_config(self):
        self.config["member_id"] = self.input_agent_id.text().strip()
        self.config["ai_partner_login_id"] = self.input_login_id.text().strip()
        self.config["ai_partner_password"] = self.input_login_pw.text().strip()
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.log(f"설정 저장 실패: {e}")

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # 왼쪽 패널
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(10, 10, 10, 10)
        
        # 인증 정보 그룹
        auth_group = QGroupBox("인증 정보")
        auth_layout = QVBoxLayout()
        self.input_agent_id = QLineEdit(self.config.get("member_id", ""))
        self.input_agent_id.setPlaceholderText("중개사 ID")
        self.input_login_id = QLineEdit(self.config.get("ai_partner_login_id", ""))
        self.input_login_id.setPlaceholderText("이실장 아이디")
        self.input_login_pw = QLineEdit(self.config.get("ai_partner_password", ""))
        self.input_login_pw.setPlaceholderText("이실장 비밀번호")
        self.input_login_pw.setEchoMode(QLineEdit.Password)
        auth_layout.addWidget(QLabel("중개사 ID:"))
        auth_layout.addWidget(self.input_agent_id)
        auth_layout.addWidget(QLabel("이실장 아이디:"))
        auth_layout.addWidget(self.input_login_id)
        auth_layout.addWidget(QLabel("이실장 비밀번호:"))
        auth_layout.addWidget(self.input_login_pw)
        auth_group.setLayout(auth_layout)
        left_panel.addWidget(auth_group)

        # 기능 실행 그룹
        func_group = QGroupBox("기능 실행")
        func_layout = QVBoxLayout()
        self.btn_check_rank = QPushButton("순위 조회 실행")
        self.btn_check_rank.setStyleSheet("background-color: #0044cc; color: white; padding: 10px; font-weight: bold;")
        self.btn_check_rank.clicked.connect(self.run_rank_check)
        
        self.btn_update_now = QPushButton("업데이트 즉시실행")
        self.btn_update_now.setStyleSheet("background-color: #FF6B6B; color: white; padding: 10px; font-weight: bold;")
        self.btn_update_now.clicked.connect(self.run_update_now)
        
        self.btn_auto = QPushButton("자동화 예약")
        self.btn_auto.setStyleSheet("background-color: #4ECDC4; color: white; padding: 10px; font-weight: bold;")
        self.btn_auto.clicked.connect(self.run_auto_schedule)

        self.btn_cancel_auto = QPushButton("자동화 예약 취소")
        self.btn_cancel_auto.setStyleSheet("background-color: #95E1D3; color: white; padding: 10px; font-weight: bold;")
        self.btn_cancel_auto.clicked.connect(self.run_cancel_auto)
        
        func_layout.addWidget(self.btn_check_rank)
        func_layout.addWidget(self.btn_update_now)
        func_layout.addWidget(self.btn_auto)
        func_layout.addWidget(self.btn_cancel_auto)
        func_group.setLayout(func_layout)
        left_panel.addWidget(func_group)

        # 로그창
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        left_panel.addWidget(QLabel("실행 로그"))
        left_panel.addWidget(self.log_view)

        # 오른쪽 패널 (테이블)
        right_panel = QVBoxLayout()
        
        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels(["선택", "매물번호", "매물명", "종류", "가격", "광고등록일", "순위", "업데이트 필요", "층수오픈", "금액변경필요"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSortingEnabled(True)
        right_panel.addWidget(self.table)

        # 하단 버튼
        bottom_buttons = QHBoxLayout()
        btn_start_automation = QPushButton("중개업 자동화 시작하기")
        btn_start_automation.setStyleSheet("background-color: #d32f2f; color: white; padding: 15px; font-size: 14px; font-weight: bold;")
        
        bottom_buttons.addWidget(btn_start_automation)
        right_panel.addLayout(bottom_buttons)

        # 전체 레이아웃 조합
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 4)

    def log(self, text):
        self.log_view.append(text)

    def run_rank_check(self):
        self.save_config()
        agent_id = self.input_agent_id.text().strip()
        if not agent_id:
            self.log("오류: 중개사 ID를 입력해주세요.")
            return
            
        self.log("프로그램 준비 완료.")
        self.log("저장된 중개사 ID를 불러왔습니다.")
        self.log(f"ID '{agent_id}'에 대한 순위 조회를 시작합니다.")
        
        self.btn_check_rank.setEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        
        self.thread = RankCheckThread(agent_id)
        self.thread.log_signal.connect(self.log)
        self.thread.result_signal.connect(self.populate_table)
        self.thread.finished_signal.connect(lambda: self.btn_check_rank.setEnabled(True))
        self.thread.start()

    def get_selected_items(self):
        # 정렬 중 인덱스 꼬임 방지를 위해 일시적으로 정렬 비활성화
        sorting_was_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        items = []
        try:
            for row in range(self.table.rowCount()):
                chk = self.table.item(row, 0)
                if chk and chk.checkState() == Qt.Checked:
                    val_item = self.table.item(row, 1)
                    if val_item:
                        items.append(val_item.text())
        finally:
            self.table.setSortingEnabled(sorting_was_enabled)
        self.log(f"[INFO] 선택된 매물 번호 ({len(items)}개): {items}")
        return items

    def _set_buttons_enabled(self, enabled):
        self.btn_check_rank.setEnabled(enabled)
        self.btn_update_now.setEnabled(enabled)
        self.btn_auto.setEnabled(enabled)
        self.btn_cancel_auto.setEnabled(enabled)

    def run_update_now(self):
        self.save_config()
        items = self.get_selected_items()
        if not items:
            QMessageBox.warning(self, "선택 확인", "업데이트를 실행할 매물을 하나 이상 선택해주세요.")
            return
            
        reply = QMessageBox.question(self, "즉시실행 확인", f"선택한 {len(items)}개의 매물을 즉시 업데이트 하시겠습니까?\n(현재 컴퓨터에서 브라우저가 보이지 않는 상태로 자동 실행됩니다.)")
        if reply == QMessageBox.Yes:
            self._set_buttons_enabled(False)
            self.ad_thread = AdUpdateThread(items, self.config)
            self.ad_thread.log_signal.connect(self.log)
            self.ad_thread.result_signal.connect(self._on_ad_finished)
            self.ad_thread.start()

    def _on_ad_finished(self, success):
        self._set_buttons_enabled(True)
        if success:
            QMessageBox.information(self, "업데이트 완료", "선택한 매물들의 즉시 업데이트가 완료되었습니다.")
        else:
            QMessageBox.warning(self, "업데이트 실패", "업데이트 중 오류가 발생했습니다. 로그를 확인해주세요.")

    def run_auto_schedule(self):
        self.save_config()
        items = self.get_selected_items()
        if not items:
            QMessageBox.warning(self, "선택 확인", "자동화 예약을 할 매물을 하나 이상 선택해주세요.")
            return
            
        reply = QMessageBox.question(self, "자동화 예약 확인", f"선택한 {len(items)}개의 매물을 자동으로 업데이트 하도록 예약하시겠습니까?")
        if reply == QMessageBox.Yes:
            self._start_github_thread("schedule", items)

    def run_cancel_auto(self):
        reply = QMessageBox.question(self, "예약 취소 확인", "현재 설정된 자동화 예약을 취소하시겠습니까?")
        if reply == QMessageBox.Yes:
            self._start_github_thread("cancel", [])

    def _start_github_thread(self, action, items):
        self._set_buttons_enabled(False)
        self.github_thread = GitHubPushThread(action, items)
        self.github_thread.log_signal.connect(self.log)
        self.github_thread.result_signal.connect(self._on_github_finished)
        self.github_thread.start()
        
    def _on_github_finished(self, action, success):
        self._set_buttons_enabled(True)
        if success:
            QMessageBox.information(self, "전송 완료", "GitHub 서버에 요청이 성공적으로 전송되었습니다.\n이제 창을 닫거나 컴퓨터를 끄셔도 알아서 실행됩니다.")

    def populate_table(self, data):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(data))
        for row, item in enumerate(data):
            # 체크박스
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk)
            
            self.table.setItem(row, 1, QTableWidgetItem(item["id"]))
            self.table.setItem(row, 2, QTableWidgetItem(item["name"]))
            self.table.setItem(row, 3, QTableWidgetItem(item["type"]))
            self.table.setItem(row, 4, QTableWidgetItem(item["price"]))
            self.table.setItem(row, 5, QTableWidgetItem(item["date"]))
            
            # 순위 숫자를 정밀하게 정렬하기 위해 setData 사용
            rank_item = QTableWidgetItem()
            try:
                rank_val = int(item["rank"])
                rank_item.setData(Qt.DisplayRole, rank_val)
            except ValueError:
                rank_item.setData(Qt.DisplayRole, item["rank"])
            self.table.setItem(row, 6, rank_item)
            
            # 업데이트 필요 (색상)
            upd = QTableWidgetItem("●")
            upd.setForeground(QColor("red") if item["update"] else QColor("green"))
            upd.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 7, upd)
            
            self.table.setItem(row, 8, QTableWidgetItem(item["floor_open"]))
            
            # 금액변경필요
            pc = QTableWidgetItem(item["price_change"])
            if item["price_change"] == "필요":
                pc.setForeground(QColor("red"))
                pc.setFont(QFont("Arial", 9, QFont.Bold))
            self.table.setItem(row, 9, pc)
        self.table.setSortingEnabled(True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NewMainWindow()
    window.show()
    sys.exit(app.exec())
