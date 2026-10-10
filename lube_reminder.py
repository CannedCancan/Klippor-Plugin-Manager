# Напоминание о смазке валов/направляющих по наработке (часам печати)
#
# Установка: положить файл в ~/klipper/klippy/extras/lube_reminder.py
# и добавить в printer.cfg секцию [lube_reminder] (см. ниже).
#
# Пример конфигурации:
#
#   [lube_reminder]
#   interval_hours: 200          # через сколько часов печати напоминать
#   check_interval: 30           # как часто (сек) проверять состояние печати
#   save_interval: 300           # как часто (сек) сохранять счётчик на диск
#   #state_file: ~/printer_data/config/lube_reminder.json
#   #message: Пора смазать валы! Отпечатано {hours} ч из {interval} ч.
#   #notify_macro: LUBE_NOTIFY   # необязательно: макрос, вызываемый при напоминании
#                                # (получает параметр HOURS), например для M117/Telegram
#
# G-code команды:
#   LUBE_STATUS          - показать наработку с последней смазки
#   LUBE_DONE            - отметить, что смазка выполнена (счётчик -> 0)
#   LUBE_SET HOURS=123.5 - вручную задать текущую наработку в часах
#
# В макросах доступно: printer.lube_reminder.due, .hours_since_lube,
# .remaining_hours, .interval_hours, .last_lube (unix time или None)

import json
import logging
import os
import time

DEFAULT_MESSAGE = ("Пора смазать валы! С последней смазки отпечатано "
                   "{hours} ч (интервал {interval} ч). "
                   "После смазки выполните LUBE_DONE.")


class LubeReminder:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')

        self.interval_hours = config.getfloat('interval_hours', 200., above=0.)
        self.check_interval = config.getfloat('check_interval', 30., above=1.)
        self.save_interval = config.getfloat('save_interval', 300., minval=10.)
        self.message = config.get('message', DEFAULT_MESSAGE)
        self.notify_macro = config.get('notify_macro', None)

        config_file = self.printer.get_start_args().get('config_file', '')
        default_path = os.path.join(os.path.dirname(config_file),
                                    'lube_reminder.json')
        self.state_path = os.path.expanduser(
            config.get('state_file', default_path))

        # Состояние (сохраняется между перезапусками)
        self.seconds_since_lube = 0.
        self.last_lube_time = None
        self._load_state()

        # Рабочие переменные
        self.print_stats = None
        self.idle_timeout = None
        self.last_tick = 0.
        self.last_save = 0.
        self.was_printing = False
        self.was_due = False
        self.dirty = False

        self.gcode.register_command(
            'LUBE_STATUS', self.cmd_LUBE_STATUS,
            desc="Показать наработку с последней смазки валов")
        self.gcode.register_command(
            'LUBE_DONE', self.cmd_LUBE_DONE,
            desc="Отметить, что валы смазаны (сброс счётчика)")
        self.gcode.register_command(
            'LUBE_SET', self.cmd_LUBE_SET,
            desc="Задать наработку вручную: LUBE_SET HOURS=<часы>")

        self.printer.register_event_handler('klippy:ready', self._handle_ready)
        self.printer.register_event_handler('klippy:shutdown', self._save_state)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._save_state)

    # ---------- хранение состояния ----------
    def _load_state(self):
        try:
            with open(self.state_path, 'r') as f:
                data = json.load(f)
            self.seconds_since_lube = float(data.get('seconds_since_lube', 0.))
            self.last_lube_time = data.get('last_lube_time')
        except FileNotFoundError:
            logging.info("lube_reminder: файл состояния не найден, "
                         "счётчик начинается с нуля")
        except Exception:
            logging.exception("lube_reminder: не удалось прочитать %s",
                              self.state_path)

    def _save_state(self, *args):
        data = {
            'seconds_since_lube': self.seconds_since_lube,
            'last_lube_time': self.last_lube_time,
        }
        tmp_path = self.state_path + '.tmp'
        try:
            with open(tmp_path, 'w') as f:
                json.dump(data, f)
            os.replace(tmp_path, self.state_path)
            self.dirty = False
        except Exception:
            logging.exception("lube_reminder: не удалось сохранить %s",
                              self.state_path)

    # ---------- логика ----------
    def _hours(self):
        return self.seconds_since_lube / 3600.

    def is_due(self):
        return self._hours() >= self.interval_hours

    def _handle_ready(self):
        self.print_stats = self.printer.lookup_object('print_stats', None)
        self.idle_timeout = self.printer.lookup_object('idle_timeout', None)
        now = self.reactor.monotonic()
        self.last_tick = now
        self.last_save = now
        self.was_due = self.is_due()
        self.reactor.register_timer(self._tick, now + self.check_interval)
        if self.was_due:
            self._notify()

    def _is_printing(self, eventtime):
        if self.print_stats is not None:
            st = self.print_stats.get_status(eventtime)
            return st.get('state') == 'printing'
        # запасной вариант, если нет [virtual_sdcard]
        if self.idle_timeout is not None:
            st = self.idle_timeout.get_status(eventtime)
            return st.get('state') == 'Printing'
        return False

    def _tick(self, eventtime):
        dt = eventtime - self.last_tick
        self.last_tick = eventtime
        # защита от больших скачков времени (засыпание хоста и т.п.)
        dt = max(0., min(dt, self.check_interval * 2.))

        printing = self._is_printing(eventtime)
        if printing:
            self.seconds_since_lube += dt
            self.dirty = True

        due = self.is_due()
        remind = False
        if printing != self.was_printing:
            # начало или конец печати: напоминаем, если пора
            remind = due
            if not printing:
                self._save_state()
                self.last_save = eventtime
        if due and not self.was_due:
            remind = True  # порог только что пересечён
        self.was_printing = printing
        self.was_due = due

        if remind:
            self._notify()

        if self.dirty and eventtime - self.last_save >= self.save_interval:
            self._save_state()
            self.last_save = eventtime

        return eventtime + self.check_interval

    def _notify(self):
        hours = self._hours()
        try:
            text = self.message.format(hours="%.1f" % hours,
                                       interval="%g" % self.interval_hours)
        except Exception:
            text = DEFAULT_MESSAGE.format(hours="%.1f" % hours,
                                          interval="%g" % self.interval_hours)
        # "!!" - вывод в консоли как предупреждение/ошибка (красным)
        self.gcode.respond_raw("!! " + text)
        if self.notify_macro:
            self.reactor.register_callback(self._run_notify_macro)

    def _run_notify_macro(self, eventtime):
        try:
            self.gcode.run_script("%s HOURS=%.1f"
                                  % (self.notify_macro, self._hours()))
        except Exception:
            logging.exception("lube_reminder: ошибка в notify_macro")

    # ---------- команды ----------
    def cmd_LUBE_STATUS(self, gcmd):
        hours = self._hours()
        remaining = self.interval_hours - hours
        if self.last_lube_time:
            last = time.strftime('%Y-%m-%d %H:%M',
                                 time.localtime(self.last_lube_time))
        else:
            last = "не отмечалась"
        if remaining > 0:
            state = "до следующей смазки осталось %.1f ч" % remaining
        else:
            state = "СМАЗКА ПРОСРОЧЕНА на %.1f ч" % (-remaining)
        gcmd.respond_info(
            "Смазка валов: отпечатано %.1f ч из %g ч, %s. "
            "Последняя смазка: %s" % (hours, self.interval_hours, state, last))

    def cmd_LUBE_DONE(self, gcmd):
        self.seconds_since_lube = 0.
        self.last_lube_time = time.time()
        self.was_due = False
        self._save_state()
        gcmd.respond_info("Смазка отмечена, счётчик сброшен. "
                          "Следующее напоминание через %g ч печати."
                          % self.interval_hours)

    def cmd_LUBE_SET(self, gcmd):
        hours = gcmd.get_float('HOURS', minval=0.)
        self.seconds_since_lube = hours * 3600.
        self.was_due = self.is_due()
        self._save_state()
        gcmd.respond_info("Наработка с последней смазки установлена: %.1f ч"
                          % hours)

    # ---------- статус для макросов / Mainsail ----------
    def get_status(self, eventtime):
        hours = self._hours()
        return {
            'hours_since_lube': round(hours, 2),
            'interval_hours': self.interval_hours,
            'remaining_hours': round(self.interval_hours - hours, 2),
            'due': self.is_due(),
            'last_lube': self.last_lube_time,
        }


def load_config(config):
    return LubeReminder(config)
