// mANY-MAZE I/O firmware — turns an Arduino into a live-test I/O box for mANY-MAZE.
// Debounced digital inputs with change reports, digital/PWM outputs (with an optional maximum on-time),
// hardware-timed pulses and pulse trains (optogenetics, pellet dispensers, sync pulses), analogue inputs (up to
// 1 kHz, sent in batches), quadrature rotary encoders, HX711 load cells (weight), DHT22 temperature / humidity
// sensors and a heartbeat watchdog. Line protocol at 115200 baud: see README.md.
//
// 1.2: the banner is printed at start-up (the computer notices a reset and configures the board again), long
// pulses and trains (period over 60 s) are timed in milliseconds, "P pin level max_ms", deadband -1 (every
// sample), over-long lines are refused, DHT22 / HX711 reads no longer mask the interrupts for milliseconds.
// 1.3: "SYNC pin width_us" (synchronisation pulses: the end of the pulse is timed by a Timer1 interrupt on AVR
// boards, so its width does not depend on what the loop is doing; "SYNC" alone repeats the last one).
//
// SPDX-License-Identifier: GPL-3.0-or-later

#define FW_VERSION "1.3"
#ifndef BOARD_NAME
#define BOARD_NAME "arduino"
#endif

const uint8_t MAX_IN = 24, MAX_OUT = 24, MAX_AN = 8, MAX_ENC = 4, MAX_TRAIN = 8, MAX_HX = 4, MAX_DHT = 4;
const uint8_t MAX_BATCH = 16;
const uint16_t ENC_REPORT_MS = 20;

struct DIn { int8_t pin; uint8_t pullup; uint16_t debounce; uint8_t stable, last; unsigned long changed; };
struct DOut { int8_t pin; uint8_t invert, pwm; uint8_t on; uint8_t timed; unsigned long offAt; };
// deadband < 0: every sample is reported (filtered channels need them all)
struct AIn { int8_t pin; uint16_t period; int16_t deadband; int last; unsigned long next;
             uint8_t batch, n; unsigned long first; int vals[MAX_BATCH]; };
struct HX { int8_t dout, sck; uint16_t period; unsigned long next; };
struct DHT { int8_t pin; uint16_t period; unsigned long next; uint8_t fails; };
struct Enc { int8_t pa, pb; volatile long count; volatile uint8_t state; long reported; unsigned long next; uint8_t irq; };
// ms = 1: period / width / start in milliseconds (long pulses), else in microseconds (wrap-safe up to ~35 min)
struct Train { int8_t pin; uint8_t active, level, ms; unsigned long period, width, count, done, start; };
const double TRAIN_MS_ABOVE = 60000.0;  // periods longer than this (ms) are timed in milliseconds

DIn ins[MAX_IN];
DOut outs[MAX_OUT];
AIn ans[MAX_AN];
Enc encs[MAX_ENC];
Train trains[MAX_TRAIN];
HX hxs[MAX_HX];
DHT dhts[MAX_DHT];
uint8_t nIn = 0, nOut = 0, nAn = 0, nEnc = 0, nHx = 0, nDht = 0;

char buf[72];
uint8_t blen = 0;
bool overflow = false;  // the line being received is longer than buf: refused when it ends
unsigned long lastRx = 0, wdMs = 0;
bool wdFired = false;

// quadrature decoding: index = (previous AB << 2) | current AB
const int8_t QDEC[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};

inline void encStep(Enc &e) {
  uint8_t ab = (digitalRead(e.pa) << 1) | digitalRead(e.pb);
  e.count += QDEC[((e.state & 3) << 2) | ab];
  e.state = ab;
}
void isrE0() { encStep(encs[0]); }
void isrE1() { encStep(encs[1]); }
void isrE2() { encStep(encs[2]); }
void isrE3() { encStep(encs[3]); }
void (*const ISRS[MAX_ENC])() = {isrE0, isrE1, isrE2, isrE3};

void err(const char *msg) {
  Serial.print(F("ERR "));
  Serial.println(msg);
}

void err(const __FlashStringHelper *msg) {  // a message kept in flash: err(F("..."))
  Serial.print(F("ERR "));
  Serial.println(msg);
}

DOut *findOut(int pin) {
  for (uint8_t i = 0; i < nOut; i++)
    if (outs[i].pin == pin) return &outs[i];
  return 0;
}

void writeOut(DOut *o, uint8_t on) {
  o->on = on;
  if (!on) o->timed = 0;
  digitalWrite(o->pin, ((on ? 1 : 0) ^ o->invert) ? HIGH : LOW);
}

void stopSync(int pin, uint8_t only = 0);
bool timer1Pin(int8_t pin);

void writePwm(DOut *o, int v) {
  if (timer1Pin(o->pin)) stopSync(-1, 2);  // a synchronisation pulse has borrowed Timer1: give it back first
  v = constrain(v, 0, 255);
  o->on = v > 0;
  if (!o->on) o->timed = 0;
  o->pwm = 1;
  analogWrite(o->pin, o->invert ? 255 - v : v);
}

void printId() {
  Serial.print(F("MANYMAZE_IO " FW_VERSION " "));
  Serial.println(F(BOARD_NAME));
}

// ---- synchronisation pulses (SYNC, 1.3) ----
// The pin goes on as soon as the command is read; on AVR boards the end of the pulse is timed by Timer1's
// compare-A interrupt, so the width is exact whatever the loop is doing (a sensor being read, a long report). Timer1
// is borrowed only for the pulse and given back as the Arduino core set it up (PWM on its pins, 9 and 10 on an Uno,
// works between pulses); while a PWM output on a Timer1 pin is on, and on other boards, the loop times the pulse
// in microseconds instead.
#if defined(__AVR__) && defined(TCCR1A) && defined(TIMSK1) && defined(OCIE1A)
#define SYNC_TIMER 1
#endif
const unsigned long SYNC_MAX_US = 1000000UL;
int8_t syncPin = -1;  // the last SYNC's pin and width ("SYNC" alone repeats them)
unsigned long syncWidth = 0;
volatile uint8_t syncOn = 0;  // a pulse is on: 1 ended by the loop, 2 by Timer1
unsigned long syncStart = 0;  // micros() when a pulse ended by the loop began

#ifdef SYNC_TIMER
volatile uint8_t *syncReg = 0;
uint8_t syncMask = 0, syncOffHigh = 0, t1a, t1b;  // the pin's port bit; Timer1 as the Arduino core set it up
uint16_t t1ocr;

inline void syncEnd() {  // (interrupts off) the pin off, Timer1 given back
  if (syncOffHigh) *syncReg |= syncMask;
  else *syncReg &= ~syncMask;
  TCCR1B = 0;
  TIMSK1 &= ~_BV(OCIE1A);
  TCCR1A = t1a;
  OCR1A = t1ocr;
  TCNT1 = 0;
  TCCR1B = t1b;
  syncOn = 0;
}

ISR(TIMER1_COMPA_vect) { syncEnd(); }  // the end of a synchronisation pulse
#endif

// the pin's PWM comes from Timer1 (AVR boards)
bool timer1Pin(int8_t pin) {
#ifdef SYNC_TIMER
  uint8_t t = digitalPinToTimer(pin);
  return t == TIMER1A || t == TIMER1B || t == TIMER1C;
#else
  (void)pin;
  return false;
#endif
}

// end a synchronisation pulse at once (pin -1: whichever; only 2: only one timed by Timer1): its output goes off
void stopSync(int pin, uint8_t only) {
  if (!syncOn || (pin >= 0 && pin != syncPin) || (only && syncOn != only)) return;
#ifdef SYNC_TIMER
  if (syncOn == 2) {
    uint8_t sreg = SREG;
    cli();
    if (syncOn == 2) syncEnd();
    SREG = sreg;
    return;
  }
#endif
  syncOn = 0;
  DOut *o = findOut(syncPin);
  if (o) writeOut(o, 0);
}

void stopTrain(int pin) {
  for (uint8_t i = 0; i < MAX_TRAIN; i++)
    if (trains[i].active && trains[i].pin == pin) trains[i].active = 0;
  stopSync(pin);
}

// a pulse of syncWidth on the output: on at once, ended by Timer1's compare interrupt when the timer is free
void syncPulse(DOut *o) {
  stopTrain(o->pin);
  o->timed = 0;
  o->pwm = 0;
#ifdef SYNC_TIMER
  bool busy = false;  // a PWM output on a Timer1 pin is on
  for (uint8_t i = 0; i < nOut; i++)
    if (outs[i].pwm && outs[i].on && timer1Pin(outs[i].pin)) busy = true;
  unsigned long cycles = syncWidth * (F_CPU / 1000000UL);
  // the smallest prescaler (8, 64, 256, 1024: CS1 values 2..5) whose 16-bit count holds the width
  for (uint8_t k = 0; k < 4 && !busy; k++) {
    unsigned long ticks = cycles >> (k == 0 ? 3 : k == 1 ? 6 : 2 * k + 4);
    if (ticks > 65536UL) continue;
    syncReg = portOutputRegister(digitalPinToPort(o->pin));
    syncMask = digitalPinToBitMask(o->pin);
    syncOffHigh = o->invert;
    uint8_t sreg = SREG;
    cli();
    t1a = TCCR1A;
    t1b = TCCR1B;
    t1ocr = OCR1A;
    TCCR1B = 0;
    TCCR1A = 0;
    TCNT1 = 0;
    OCR1A = ticks ? (uint16_t)(ticks - 1) : 0;
    TIFR1 = _BV(OCF1A);
    TIMSK1 |= _BV(OCIE1A);
    if (o->invert) *syncReg &= ~syncMask;  // on
    else *syncReg |= syncMask;
    TCCR1B = _BV(WGM12) | (k + 2);  // CTC: the compare match ends the pulse
    syncOn = 2;
    SREG = sreg;
    return;
  }
#endif
  writeOut(o, 1);  // other boards, or Timer1 busy: the loop ends the pulse
  syncStart = micros();
  syncOn = 1;
}

void allOff() {
  stopSync(-1);
  for (uint8_t i = 0; i < MAX_TRAIN; i++) trains[i].active = 0;
  for (uint8_t i = 0; i < nOut; i++) {
    if (outs[i].pwm) writePwm(&outs[i], 0);
    else writeOut(&outs[i], 0);
  }
}

void reportIn(DIn &d) {
  Serial.print(F("D "));
  Serial.print(d.pin);
  Serial.print(' ');
  Serial.print(d.stable);
  Serial.print(' ');
  Serial.println(millis());
}

void reportAn(AIn &a) {
  Serial.print(F("A "));
  Serial.print(a.pin);
  Serial.print(' ');
  Serial.print(a.last);
  Serial.print(' ');
  Serial.println(millis());
}

// a batch of analogue samples: S n first_ms period_us v1 v2 ...
void reportBatch(AIn &a) {
  Serial.print(F("S "));
  Serial.print(a.pin);
  Serial.print(' ');
  Serial.print(a.first);
  Serial.print(' ');
  Serial.print((unsigned long)a.period * 1000UL);
  for (uint8_t i = 0; i < a.n; i++) {
    Serial.print(' ');
    Serial.print(a.vals[i]);
  }
  Serial.println();
  a.n = 0;
}

// one HX711 clock pulse; returns DOUT read while SCK is high. Interrupts are off only during the pulse: SCK high
// for more than 60 us would power the HX711 down, but the serial port, encoders and timers keep running between bits
uint8_t hxPulse(HX &h) {
  noInterrupts();
  digitalWrite(h.sck, HIGH);
  delayMicroseconds(1);
  uint8_t b = digitalRead(h.dout);
  digitalWrite(h.sck, LOW);
  interrupts();
  delayMicroseconds(1);
  return b;
}

// HX711 load-cell amplifier (channel A, gain 128): 24-bit signed reading, or false if not ready
bool readHX(HX &h, long &value) {
  if (digitalRead(h.dout)) return false;
  unsigned long v = 0;
  for (uint8_t i = 0; i < 24; i++) v = (v << 1) | hxPulse(h);
  hxPulse(h);  // 25th pulse: channel A, gain 128 next time
  if (v & 0x800000UL) v |= 0xFF000000UL;
  value = (long)v;
  return true;
}

// length in microseconds of the current level of a pin, or -1 after timeout_us
long levelLength(int8_t pin, uint8_t level, unsigned long timeout_us) {
  unsigned long start = micros();
  while (digitalRead(pin) == level)
    if (micros() - start > timeout_us) return -1;
  return (long)(micros() - start);
}

// DHT22 / AM2302: temperature and humidity in tenths, or false on a timeout / checksum error. Read with the
// interrupts on (the serial port must not lose bytes, nor the encoders edges, during the ~5 ms of a reading): an
// interrupt that stretches a bit may corrupt a reading now and then, which the checksum catches (see loop()).
bool readDHT(DHT &d, int &t10, int &h10) {
  uint8_t data[5] = {0, 0, 0, 0, 0};
  pinMode(d.pin, OUTPUT);  // start signal: at least 1 ms low
  digitalWrite(d.pin, LOW);
  delayMicroseconds(1200);
  pinMode(d.pin, INPUT_PULLUP);
  // the line floats high 20-40 us, then the sensor answers ~80 us low and ~80 us high
  bool ok = levelLength(d.pin, HIGH, 200) >= 0 && levelLength(d.pin, LOW, 200) >= 0 &&
            levelLength(d.pin, HIGH, 200) >= 0;
  // 40 bits: ~50 us low, then high for ~26 us (0) or ~70 us (1)
  for (uint8_t i = 0; ok && i < 40; i++) {
    long high = levelLength(d.pin, LOW, 200) >= 0 ? levelLength(d.pin, HIGH, 200) : -1;
    if (high < 0) ok = false;
    else data[i / 8] = (data[i / 8] << 1) | (high > 40 ? 1 : 0);
  }
  if (!ok || ((data[0] + data[1] + data[2] + data[3]) & 0xFF) != data[4]) return false;
  h10 = ((int)data[0] << 8) | data[1];
  t10 = (((int)(data[2] & 0x7F)) << 8) | data[3];
  if (data[2] & 0x80) t10 = -t10;
  return true;
}

void reportEnc(Enc &e, long c) {
  e.reported = c;
  Serial.print(F("E "));
  Serial.print(e.pa);
  Serial.print(' ');
  Serial.print(c);
  Serial.print(' ');
  Serial.println(millis());
}

long encCount(Enc &e) {
  noInterrupts();
  long c = e.count;
  interrupts();
  return c;
}

void clearConfig() {
  allOff();
  for (uint8_t i = 0; i < nEnc; i++)
    if (encs[i].irq) {
      detachInterrupt(digitalPinToInterrupt(encs[i].pa));
      detachInterrupt(digitalPinToInterrupt(encs[i].pb));
    }
  nIn = nOut = nAn = nEnc = nHx = nDht = 0;
  syncPin = -1;
  syncWidth = 0;
  wdMs = 0;
}

void command(char *line) {
  char *argv[6];
  uint8_t argc = 0;
  for (char *tok = strtok(line, " \t"); tok && argc < 6; tok = strtok(0, " \t")) argv[argc++] = tok;
  if (!argc) return;
  char c = argv[0][0];
  long a1 = argc > 1 ? atol(argv[1]) : 0;
  long a2 = argc > 2 ? atol(argv[2]) : 0;
  long a3 = argc > 3 ? atol(argv[3]) : 0;
  switch (c) {
    case '?':
      printId();
      break;
    case '.':
      break;  // heartbeat
    case 'Z':
      clearConfig();
      break;
    case 'R':
      allOff();
      break;
    case 'I': {  // I pin pullup debounce_ms
      if (argc < 2 || nIn >= MAX_IN) { err("I: bad arguments or too many inputs"); break; }
      DIn &d = ins[nIn++];
      d.pin = a1;
      d.pullup = argc > 2 ? a2 : 1;
      d.debounce = argc > 3 ? a3 : 20;
      pinMode(d.pin, d.pullup ? INPUT_PULLUP : INPUT);
      d.stable = d.last = digitalRead(d.pin);
      d.changed = millis();
      reportIn(d);
      break;
    }
    case 'O': {  // O pin invert
      if (argc < 2) { err("O: missing pin"); break; }
      DOut *o = findOut(a1);
      if (!o) {
        if (nOut >= MAX_OUT) { err("O: too many outputs"); break; }
        o = &outs[nOut++];
      }
      o->pin = a1;
      o->invert = argc > 2 ? a2 : 0;
      o->pwm = 0;
      pinMode(o->pin, OUTPUT);
      writeOut(o, 0);
      break;
    }
    case 'W': {  // W pin 0|1 [max_ms]
      DOut *o = findOut(a1);
      if (!o || argc < 3) { err("W: pin not configured as an output"); break; }
      stopTrain(o->pin);
      o->pwm = 0;
      o->timed = 0;  // a previous maximum on-time never applies to this command
      writeOut(o, a2 ? 1 : 0);
      if (a2 && argc > 3 && a3 > 0) {
        o->timed = 1;
        o->offAt = millis() + (unsigned long)a3;
      }
      break;
    }
    case 'P': {  // P pin 0..255 [max_ms]
      DOut *o = findOut(a1);
      if (!o || argc < 3) { err("P: pin not configured as an output"); break; }
      stopTrain(o->pin);
      o->timed = 0;
      writePwm(o, a2);
      if (a2 > 0 && argc > 3 && a3 > 0) {
        o->timed = 1;
        o->offAt = millis() + (unsigned long)a3;
      }
      break;
    }
    case 'T': {  // T pin period_ms width_ms count   (count 0 = until X)
      DOut *o = findOut(a1);
      if (!o || argc < 5) { err("T: pin not configured or missing arguments"); break; }
      stopTrain(o->pin);
      int8_t slot = -1;
      for (uint8_t i = 0; i < MAX_TRAIN; i++)
        if (!trains[i].active) { slot = i; break; }
      if (slot < 0) { err("T: too many pulse trains"); break; }
      Train &t = trains[slot];
      double per = atof(argv[2]), wid = atof(argv[3]);
      if (per < wid) per = wid;
      t.pin = a1;
      t.ms = per > TRAIN_MS_ABOVE;  // microseconds would overflow the wrap-safe comparison past ~35 min
      if (t.ms) {
        t.period = (unsigned long)(per + 0.5);
        t.width = (unsigned long)(wid + 0.5);
        if (t.width < 1) t.width = 1;
        if (t.period < t.width) t.period = t.width;
        t.start = millis();
      } else {
        t.period = (unsigned long)(per * 1000.0);
        t.width = (unsigned long)(wid * 1000.0);
        if (t.width < 50) t.width = 50;
        if (t.period < t.width) t.period = t.width;
        t.start = micros();
      }
      t.count = atol(argv[4]);
      t.done = 0;
      t.level = 0;
      o->pwm = 0;
      o->timed = 0;
      t.active = 1;
      break;
    }
    case 'X': {  // X pin
      DOut *o = findOut(a1);
      stopTrain(a1);
      if (o) writeOut(o, 0);
      break;
    }
    case 'A': {  // A channel period_ms deadband [batch]   (deadband -1: every sample)
      if (argc < 2 || nAn >= MAX_AN) { err("A: bad arguments or too many analogue inputs"); break; }
      AIn &a = ans[nAn++];
      a.pin = a1;
      a.period = argc > 2 && a2 > 0 ? a2 : 50;
      a.deadband = argc > 3 ? (a3 < 0 ? -1 : (int16_t)constrain(a3, 0, 1023)) : 2;
      a.batch = argc > 4 ? constrain(atol(argv[4]), 1, MAX_BATCH) : 1;
      a.n = 0;
      a.last = analogRead(a.pin);
      a.next = millis() + a.period;
      reportAn(a);
      break;
    }
    case 'L': {  // L dout sck period_ms   HX711 load cell
      if (argc < 3 || nHx >= MAX_HX) { err("L: bad arguments or too many load cells"); break; }
      HX &h = hxs[nHx++];
      h.dout = a1;
      h.sck = a2;
      h.period = argc > 3 && a3 >= 100 ? a3 : 100;
      pinMode(h.dout, INPUT);
      pinMode(h.sck, OUTPUT);
      digitalWrite(h.sck, LOW);
      h.next = millis();
      break;
    }
    case 'U': {  // U pin period_ms   DHT22 temperature / humidity
      if (argc < 2 || nDht >= MAX_DHT) { err("U: bad arguments or too many DHT sensors"); break; }
      DHT &d = dhts[nDht++];
      d.pin = a1;
      d.fails = 0;
      d.period = argc > 2 && a2 >= 2000 ? a2 : 2000;
      pinMode(d.pin, INPUT_PULLUP);
      d.next = millis() + 1000;  // the sensor needs ~1 s after power-up
      break;
    }
    case 'E': {  // E pinA pinB
      if (argc < 3 || nEnc >= MAX_ENC) { err("E: bad arguments or too many encoders"); break; }
      Enc &e = encs[nEnc];
      e.pa = a1;
      e.pb = a2;
      pinMode(e.pa, INPUT_PULLUP);
      pinMode(e.pb, INPUT_PULLUP);
      e.state = (digitalRead(e.pa) << 1) | digitalRead(e.pb);
      e.count = 0;
      e.reported = 0;
      e.next = 0;
      e.irq = digitalPinToInterrupt(e.pa) != NOT_AN_INTERRUPT && digitalPinToInterrupt(e.pb) != NOT_AN_INTERRUPT;
      if (e.irq) {
        attachInterrupt(digitalPinToInterrupt(e.pa), ISRS[nEnc], CHANGE);
        attachInterrupt(digitalPinToInterrupt(e.pb), ISRS[nEnc], CHANGE);
      }
      nEnc++;
      reportEnc(e, 0);
      break;
    }
    case 'Q':  // report every input now
      for (uint8_t i = 0; i < nIn; i++) reportIn(ins[i]);
      for (uint8_t i = 0; i < nAn; i++) reportAn(ans[i]);
      for (uint8_t i = 0; i < nEnc; i++) reportEnc(encs[i], encCount(encs[i]));
      break;
    case 'H':  // H timeout_ms (0 = off)
      wdMs = a1;
      wdFired = false;
      break;
    case 'S': {  // SYNC pin width_us   (SYNC alone: the last pin and width again)
      char *w = argv[0];
      if (w[1] != 'Y' || w[2] != 'N' || w[3] != 'C' || w[4]) { err("unknown command"); break; }
      stopSync(-1);  // (before the pin changes)
      if (argc >= 3) {
        syncPin = a1;
        syncWidth = a2 < 1 ? 1 : a2 > (long)SYNC_MAX_US ? SYNC_MAX_US : (unsigned long)a2;
      }
      DOut *o = syncPin >= 0 ? findOut(syncPin) : 0;
      if (!o) { err(F("SYNC: not an output")); break; }
      syncPulse(o);
      break;
    }
    default:
      err("unknown command");
  }
}

void setup() {
  Serial.begin(115200);
  lastRx = millis();
  // the banner at start-up: a board that restarts during a test (power or USB glitch) has lost its configuration
  // and its outputs; mANY-MAZE sees the banner arrive unasked and configures it again
  printId();
}

void loop() {
  // serial commands; a line longer than the buffer is refused whole (never executed truncated)
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (overflow) {
        overflow = false;
        blen = 0;
        lastRx = millis();
        wdFired = false;
        err("line too long");
      } else if (blen) {
        buf[blen] = 0;
        lastRx = millis();
        wdFired = false;
        command(buf);
        blen = 0;
      }
    } else if (blen < sizeof(buf) - 1) {
      buf[blen++] = ch;
    } else {
      overflow = true;
    }
  }
  unsigned long now = millis();
  unsigned long us = micros();
  // pulse trains (microsecond timing, or millisecond timing for long periods; wrap-safe)
  for (uint8_t i = 0; i < MAX_TRAIN; i++) {
    Train &t = trains[i];
    if (!t.active) continue;
    DOut *o = findOut(t.pin);
    if (!o) { t.active = 0; continue; }
    unsigned long clk = t.ms ? now : us;
    unsigned long onT = t.start + t.done * t.period;
    if (!t.level) {
      if (t.count && t.done >= t.count) { t.active = 0; continue; }
      if ((long)(clk - onT) >= 0) { writeOut(o, 1); t.level = 1; }
    } else if ((long)(clk - (onT + t.width)) >= 0) {
      writeOut(o, 0);
      t.level = 0;
      t.done++;
    }
  }
  // a synchronisation pulse timed by the loop (no free timer)
  if (syncOn == 1 && us - syncStart >= syncWidth) stopSync(-1);
  // outputs with a maximum on-time (e.g. shock safety cut-off), digital or PWM
  for (uint8_t i = 0; i < nOut; i++)
    if (outs[i].timed && outs[i].on && (long)(now - outs[i].offAt) >= 0) {
      if (outs[i].pwm) writePwm(&outs[i], 0);
      else writeOut(&outs[i], 0);
    }
  // debounced digital inputs
  for (uint8_t i = 0; i < nIn; i++) {
    DIn &d = ins[i];
    uint8_t raw = digitalRead(d.pin);
    if (raw != d.last) { d.last = raw; d.changed = now; }
    if (raw != d.stable && now - d.changed >= d.debounce) { d.stable = raw; reportIn(d); }
  }
  // analogue inputs (fast ones in batches: one line per ~10 ms instead of one per sample)
  for (uint8_t i = 0; i < nAn; i++) {
    AIn &a = ans[i];
    if ((long)(now - a.next) < 0) continue;
    a.next += a.period;
    if ((long)(now - a.next) > (long)(4 * a.period)) a.next = now + a.period;  // fell behind: resynchronise
    int v = analogRead(a.pin);
    if (a.batch > 1) {
      if (!a.n) a.first = now;
      a.vals[a.n++] = v;
      a.last = v;
      if (a.n >= a.batch) reportBatch(a);
    } else if (a.deadband < 0 || abs(v - a.last) > a.deadband) {
      a.last = v;
      reportAn(a);
    }
  }
  // load cells
  for (uint8_t i = 0; i < nHx; i++) {
    HX &h = hxs[i];
    if ((long)(now - h.next) < 0) continue;
    long v;
    if (readHX(h, v)) {
      h.next = now + h.period;
      Serial.print(F("L "));
      Serial.print(h.dout);
      Serial.print(' ');
      Serial.print(v);
      Serial.print(' ');
      Serial.println(now);
    }
  }
  // temperature / humidity sensors
  for (uint8_t i = 0; i < nDht; i++) {
    DHT &d = dhts[i];
    if ((long)(now - d.next) < 0) continue;
    d.next = now + d.period;
    int t10, h10;
    if (readDHT(d, t10, h10)) {
      d.fails = 0;
      Serial.print(F("U "));
      Serial.print(d.pin);
      Serial.print(' ');
      Serial.print(t10);
      Serial.print(' ');
      Serial.print(h10);
      Serial.print(' ');
      Serial.println(now);
    } else if (++d.fails >= 3) {  // a corrupted reading now and then is normal: reported after three in a row
      d.fails = 0;
      err("U: no reply from the DHT22");
    }
  }
  // encoders (polled when the pins have no interrupt)
  for (uint8_t i = 0; i < nEnc; i++) {
    Enc &e = encs[i];
    if (!e.irq) encStep(e);
    if ((long)(now - e.next) >= 0) {
      long c = encCount(e);
      if (c != e.reported) { reportEnc(e, c); e.next = now + ENC_REPORT_MS; }
    }
  }
  // heartbeat watchdog
  if (wdMs && !wdFired && now - lastRx > wdMs) {
    allOff();
    wdFired = true;
    Serial.println(F("WATCHDOG"));
  }
}
