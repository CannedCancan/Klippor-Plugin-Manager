# heat_soak_guard - не даёт печати начаться, пока принтер не прогрелся.
#
# ЗАЧЕМ: чтобы печать стартовала, только когда рама (корпус) нагрета до нужной
# температуры, а стол уже достаточно долго держит свою температуру.
#
# КАК ПОЛЬЗОВАТЬСЯ: добавьте команду HEAT_SOAK_WAIT в стартовый G-код,
# после того как включён и прогрет стол:
#   M140 S100        - включить нагрев стола до 100
#   M190 S100        - дождаться, пока стол нагреется
#   HEAT_SOAK_WAIT   - ждать прогрева рамы и выдержки стола
# Печать пойдёт дальше, только когда выполнены ОБА условия:
#   1) температура рамы в пределах frame_target ± frame_tolerance;
#   2) температура стола в пределах ± bed_tolerance от его уставки (то, что
#      задано в M140/M190) и держится так без перерыва bed_minutes минут.
# Если стол выйдет из допуска, отсчёт минут начнётся заново.
# Если стол уже прогрет заранее, команда пропустит ожидание сразу.
#
# НАСТРОЙКИ. Строки без "#" действуют, строки с "# " - необязательные
# (уберите "# " в начале, чтобы включить; в скобках указано значение по умолчанию).
#   [heat_soak_guard]
#   # ОБЯЗАТЕЛЬНО: датчик температуры рамы. Впишите имя вашей секции датчика
#   # из printer.cfg, например "temperature_sensor frame"
#   frame_sensor: temperature_sensor frame
#   # ОБЯЗАТЕЛЬНО: какая температура рамы нужна, в °C
#   frame_target: 45
#   # Допуск по раме, °C: рама считается прогретой, если от 40 до 50 при цели 45 (5)
#   frame_tolerance: 5
#   # Допуск по столу, °C: стол считается "в норме", если отличается от уставки
#   # не больше чем на это значение (2)
#   bed_tolerance: 2
#   # Сколько минут подряд стол должен быть "в норме" перед печатью (10)
#   bed_minutes: 10
#   # Сколько минут подряд рама должна быть "в норме". 0 - достаточно того,
#   # что рама в допуске прямо сейчас (0)
#   # frame_minutes: 0
#   # Максимальное время ожидания, мин. Если не дождались - печать остановится
#   # с ошибкой. 0 - ждать бесконечно (180)
#   # wait_timeout_minutes: 180
#   # Имя нагревателя стола, если он называется не heater_bed (heater_bed)
#   # bed_heater: heater_bed
#   # Включено ли ожидание. False - команда HEAT_SOAK_WAIT ничего не делает (True)
#   # enabled: True
#   # Проверять прогрев ещё и при запуске файла из Mainsail/Fluidd, до стартового
#   # G-кода. Для обычного сценария с HEAT_SOAK_WAIT не нужно (False)
#   # guard_start: False
#   # Только при guard_start: True. wait - отложить печать до прогрева,
#   # reject - сразу отказать с ошибкой (wait)
#   # mode: wait
#
# КОМАНДЫ (вводятся в консоли или в G-коде):
#   HEAT_SOAK_WAIT        - ждать прогрева; можно временно задать другие значения:
#                           HEAT_SOAK_WAIT FRAME=45 BED_MINUTES=15 TIMEOUT=60
#                           (FRAME - цель по раме, BED_MINUTES - выдержка стола,
#                           TIMEOUT - максимум ожидания в минутах)
#   HEAT_SOAK_STATUS      - показать температуры и сколько ещё ждать
#   HEAT_SOAK_SET ENABLE=0 - выключить ожидание (ENABLE=1 - включить обратно);
#                           так же можно менять FRAME, FRAME_TOL, BED_TOL,
#                           BED_MINUTES, FRAME_MINUTES, MODE. Действует до
#                           перезапуска Klipper.
#   HEAT_SOAK_SKIP, HEAT_SOAK_CANCEL - только для guard_start: запустить
#                           отложенную печать сразу / отменить её.
#
# ВАЖНО: пока идёт ожидание, кнопки Pause/Cancel в интерфейсе не срабатывают
# (как и при M190). Срочно остановить - M112 (аварийная остановка).
import logging

POLL = 2.0      # период опроса датчиков, с
REPORT = 60.0   # как часто писать статус в консоль при ожидании, с


class HeatSoakGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.bed_name = config.get('bed_heater', 'heater_bed')
        self.frame_name = config.get('frame_sensor')
        self.frame_target = config.getfloat('frame_target', above=0.)
        self.frame_tol = config.getfloat('frame_tolerance', 3., above=0.)
        self.bed_tol = config.getfloat('bed_tolerance', 2., above=0.)
        self.bed_min = config.getfloat('bed_minutes', 10., minval=0.)
        self.frame_min = config.getfloat('frame_minutes', 0., minval=0.)
        self.mode = config.getchoice(
            'mode', {'wait': 'wait', 'reject': 'reject'}, 'wait')
        self.timeout = config.getfloat('wait_timeout_minutes', 180., minval=0.)
        self.enabled = config.getboolean('enabled', True)
        self.guard_start = config.getboolean('guard_start', False)
        self.bed = self.frame = None
        self.prev = {}
        self.bypass = False
        # показания и "с какого момента в норме"
        self.bed_temp = self.bed_target = self.frame_temp = 0.
        self.bed_since = self.frame_since = None
        # отложенный запуск печати
        self.pending = None
        self.pending_since = self.last_report = 0.
        self.force = False
        self.timer = self.reactor.register_timer(self._tick,
                                                 self.reactor.NEVER)
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('klippy:ready', self._ready)
        for cmd, fn, desc in (
                ('HEAT_SOAK_WAIT', self.cmd_WAIT,
                 "Ждать прогрева стола и рамы"),
                ('HEAT_SOAK_STATUS', self.cmd_STATUS, "Состояние прогрева"),
                ('HEAT_SOAK_SET', self.cmd_SET, "Временные настройки"),
                ('HEAT_SOAK_SKIP', self.cmd_SKIP, "Запустить печать сейчас"),
                ('HEAT_SOAK_CANCEL', self.cmd_CANCEL, "Отменить ожидание")):
            self.gcode.register_command(cmd, fn, desc=desc)

    # ---------- инициализация ----------
    def _connect(self):
        self.bed = self.printer.lookup_object(self.bed_name, None)
        self.frame = self.printer.lookup_object(self.frame_name, None)
        if self.bed is None:
            raise self.printer.config_error(
                "heat_soak_guard: не найден нагреватель '%s'" % self.bed_name)
        if self.frame is None:
            raise self.printer.config_error(
                "heat_soak_guard: не найден датчик '%s'" % self.frame_name)
        if not self.guard_start:
            return
        if self.printer.lookup_object('virtual_sdcard', None) is None:
            raise self.printer.config_error(
                "heat_soak_guard: нужна секция [virtual_sdcard]")
        # перехватываем команды старта печати, запоминая прежние обработчики
        for name in ('SDCARD_PRINT_FILE', 'M24'):
            old = self.gcode.register_command(name, None)
            if old is None:
                continue
            self.prev[name] = old
            self.gcode.register_command(name, self._make_handler(name),
                                        desc="Старт печати (с проверкой "
                                             "прогрева)")

    def _ready(self):
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    # ---------- измерения ----------
    def _update(self, eventtime):
        bed = self.bed.get_status(eventtime)
        self.bed_temp, self.bed_target = bed['temperature'], bed['target']
        self.frame_temp = self.frame.get_status(eventtime)['temperature']
        bed_ok = (self.bed_target > 0.
                  and abs(self.bed_temp - self.bed_target) <= self.bed_tol)
        frame_ok = abs(self.frame_temp - self.frame_target) <= self.frame_tol
        if not bed_ok:
            self.bed_since = None
        elif self.bed_since is None:
            self.bed_since = eventtime
        if not frame_ok:
            self.frame_since = None
        elif self.frame_since is None:
            self.frame_since = eventtime

    @staticmethod
    def _part(label, temp, target, tol, since, need_min, now):
        if since is None:
            return False, "%s %.1f°C (цель %.1f±%.1f)" % (label, temp,
                                                          target, tol)
        held = (now - since) / 60.
        if held < need_min:
            return False, "%s в норме %.1f из %.1f мин (%.1f°C)" % (
                label, held, need_min, temp)
        return True, "%s ок (%.1f°C)" % (label, temp)

    def _evaluate(self, now):
        """(готово, текст) по последним показаниям."""
        if self.bed_target <= 0.:
            bed_ok, bed_txt = False, "нагрев стола выключен (задайте M140)"
        else:
            bed_ok, bed_txt = self._part("стол", self.bed_temp,
                                         self.bed_target, self.bed_tol,
                                         self.bed_since, self.bed_min, now)
        frame_ok, frame_txt = self._part("рама", self.frame_temp,
                                         self.frame_target, self.frame_tol,
                                         self.frame_since, self.frame_min, now)
        return bed_ok and frame_ok, bed_txt + "; " + frame_txt

    # ---------- перехват старта печати ----------
    def _is_busy(self, eventtime):
        ps = self.printer.lookup_object('print_stats', None)
        state = ps.get_status(eventtime)['state'] if ps else ''
        return state in ('printing', 'paused')   # возобновление не трогаем

    def _make_handler(self, name):
        def handler(gcmd):
            prev = self.prev[name]
            if self.bypass:
                self.bypass = False
                return prev(gcmd)
            now = self.reactor.monotonic()
            if not self.enabled or self._is_busy(now):
                return prev(gcmd)
            if self.pending is not None:
                raise gcmd.error("Печать уже ожидает прогрева "
                                 "(HEAT_SOAK_CANCEL - отмена)")
            self._update(now)
            ok, text = self._evaluate(now)
            if ok:
                return prev(gcmd)
            if self.bed_target <= 0. or self.mode == 'reject':
                raise gcmd.error("Печать заблокирована, нет прогрева: " + text)
            self.pending = gcmd.get_commandline()
            self.pending_since = self.last_report = now
            gcmd.respond_info("Печать отложена до прогрева: %s. "
                              "HEAT_SOAK_SKIP - начать сейчас, "
                              "HEAT_SOAK_CANCEL - отменить" % text)
        return handler

    # ---------- фоновый опрос ----------
    def _tick(self, eventtime):
        try:
            self._update(eventtime)
            if self.pending is not None:
                self._check_pending(eventtime)
        except Exception:
            logging.exception("heat_soak_guard: ошибка опроса")
        return eventtime + POLL

    def _check_pending(self, now):
        if not self.enabled or self.force:
            return self._release("проверка пропущена")
        if self.bed_target <= 0.:
            self.pending = None
            self.gcode.respond_info("Нагрев стола выключен - ожидание "
                                    "отменено, печать не запущена")
            return
        ok, text = self._evaluate(now)
        if ok:
            return self._release("прогрев завершён")
        if self.timeout and now - self.pending_since > self.timeout * 60.:
            self.pending = None
            self.gcode.respond_info("Превышено время ожидания (%.0f мин), "
                                    "печать не запущена. %s"
                                    % (self.timeout, text))
        elif now - self.last_report >= REPORT:
            self.last_report = now
            self.gcode.respond_info("Ожидание прогрева: " + text)

    def _release(self, why):
        cmd, self.pending, self.force = self.pending, None, False
        self.gcode.respond_info("Запуск печати: " + why)
        self.reactor.register_async_callback(lambda e: self._start(cmd))

    def _start(self, cmd):
        self.bypass = True
        try:
            self.gcode.run_script(cmd)   # ошибку Klipper покажет сам
        except Exception:
            logging.exception("heat_soak_guard: не удалось запустить печать")
        finally:
            self.bypass = False

    # ---------- команды ----------
    def cmd_WAIT(self, gcmd):
        if not self.enabled:
            gcmd.respond_info("heat_soak_guard выключен - ожидание пропущено")
            return
        frame = gcmd.get_float('FRAME', self.frame_target, above=0.)
        bed_min = gcmd.get_float('BED_MINUTES', self.bed_min, minval=0.)
        timeout = gcmd.get_float('TIMEOUT', self.timeout, minval=0.)
        old = self.frame_target, self.bed_min
        self.frame_target, self.bed_min = frame, bed_min
        try:
            start = last = self.reactor.monotonic()
            while True:
                now = self.reactor.monotonic()
                if self.printer.is_shutdown():
                    raise gcmd.error("Klipper остановлен")
                self._update(now)
                if self.bed_target <= 0.:
                    raise gcmd.error("Нагрев стола выключен - нечего ждать "
                                     "(задайте M140/M190 перед "
                                     "HEAT_SOAK_WAIT)")
                ok, text = self._evaluate(now)
                if ok:
                    gcmd.respond_info("Прогрев завершён: " + text)
                    return
                if timeout and now - start > timeout * 60.:
                    raise gcmd.error("Прогрев не достигнут за %.0f мин: %s"
                                     % (timeout, text))
                if now - last >= REPORT or now == start:
                    last = now
                    gcmd.respond_info("Ожидание прогрева: " + text)
                self.reactor.pause(now + 1.)
        finally:
            self.frame_target, self.bed_min = old

    def cmd_STATUS(self, gcmd):
        now = self.reactor.monotonic()
        self._update(now)
        ok, text = self._evaluate(now)
        gcmd.respond_info("heat_soak_guard: %s, режим %s. %s -> %s%s" % (
            "вкл" if self.enabled else "выкл", self.mode, text,
            "печать разрешена" if ok else "печать запрещена",
            " (печать ожидает)" if self.pending else ""))

    def cmd_SET(self, gcmd):
        mode = gcmd.get('MODE', self.mode)
        if mode not in ('wait', 'reject'):
            raise gcmd.error("MODE должен быть wait или reject")
        self.mode = mode
        self.enabled = bool(gcmd.get_int('ENABLE', int(self.enabled),
                                         minval=0, maxval=1))
        self.frame_target = gcmd.get_float('FRAME', self.frame_target,
                                           above=0.)
        self.frame_tol = gcmd.get_float('FRAME_TOL', self.frame_tol, above=0.)
        self.bed_tol = gcmd.get_float('BED_TOL', self.bed_tol, above=0.)
        self.bed_min = gcmd.get_float('BED_MINUTES', self.bed_min, minval=0.)
        self.frame_min = gcmd.get_float('FRAME_MINUTES', self.frame_min,
                                        minval=0.)
        gcmd.respond_info(
            "heat_soak_guard: %s, режим %s, рама %.1f±%.1f (%.1f мин), "
            "стол ±%.1f (%.1f мин)" % (
                "вкл" if self.enabled else "выкл", self.mode,
                self.frame_target, self.frame_tol, self.frame_min,
                self.bed_tol, self.bed_min))

    def cmd_SKIP(self, gcmd):
        if self.pending is None:
            raise gcmd.error("Нет отложенной печати")
        self.force = True

    def cmd_CANCEL(self, gcmd):
        if self.pending is None:
            raise gcmd.error("Нет отложенной печати")
        self.pending = None
        gcmd.respond_info("Отложенная печать отменена")

    def get_status(self, eventtime):
        ok, text = self._evaluate(eventtime)
        return {'enabled': self.enabled, 'ready': ok, 'text': text,
                'pending': self.pending is not None,
                'bed_temp': self.bed_temp, 'bed_target': self.bed_target,
                'frame_temp': self.frame_temp,
                'frame_target': self.frame_target}


def load_config(config):
    return HeatSoakGuard(config)
