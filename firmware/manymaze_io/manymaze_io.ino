// mANY-MAZE I/O firmware — turns an Arduino into a live-test I/O box for mANY-MAZE.
// Debounced digital inputs with change reports, digital/PWM outputs (with an optional maximum on-time),
// hardware-timed pulses and pulse trains (optogenetics, pellet dispensers, sync pulses), analogue inputs,
// quadrature rotary encoders and a heartbeat watchdog. Line protocol at 115200 baud: see README.md.
//
// SPDX-License-Identifier: GPL-3.0-or-later

#define FW_VERSION "1.0"
#ifndef BOARD_NAME
#define BOARD_NAME "arduino"
#endif

const uint8_t MAX_IN = 24, MAX_OUT = 24, MAX_AN = 8, MAX_ENC = 4, MAX_TRAIN = 8;
const uint16_t ENC_REPORT_MS = 20;

struct DIn { int8_t pin; uint8_t pullup; uint16_t debounce; uint8_t stable, last; unsigned long changed; };
struct DOut { int8_t pin; uint8_t invert, pwm; uint8_t on; uint8_t timed; unsigned long offAt; };
struct AIn { int8_t pin; uint16_t period, deadband; int last; unsigned long next; };
struct Enc { int8_t pa, pb; volatile long count; volatile uint8_t state; long reported; unsigned long next; uint8_t irq; };
struct Train { int8_t pin; uint8_t active, level; unsigned long period, width, count, done, start; };

DIn ins[MAX_IN];
DOut outs[MAX_OUT];
AIn ans[MAX_AN];
Enc encs[MAX_ENC];
Train trains[MAX_TRAIN];
uint8_t nIn = 0, nOut = 0, nAn = 0, nEnc = 0;

char buf[72];
uint8_t blen = 0;
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

void writePwm(DOut *o, int v) {
  v = constrain(v, 0, 255);
  o->on = v > 0;
  o->pwm = 1;
  analogWrite(o->pin, o->invert ? 255 - v : v);
}

void stopTrain(int pin) {
  for (uint8_t i = 0; i < MAX_TRAIN; i++)
    if (trains[i].active && trains[i].pin == pin) trains[i].active = 0;
}

void allOff() {
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
  nIn = nOut = nAn = nEnc = 0;
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
      Serial.print(F("MANYMAZE_IO " FW_VERSION " "));
      Serial.println(F(BOARD_NAME));
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
      writeOut(o, a2 ? 1 : 0);
      if (a2 && argc > 3 && a3 > 0) {
        o->timed = 1;
        o->offAt = millis() + (unsigned long)a3;
      }
      break;
    }
    case 'P': {  // P pin 0..255
      DOut *o = findOut(a1);
      if (!o || argc < 3) { err("P: pin not configured as an output"); break; }
      stopTrain(o->pin);
      writePwm(o, a2);
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
      t.pin = a1;
      t.period = (unsigned long)(atof(argv[2]) * 1000.0);
      t.width = (unsigned long)(atof(argv[3]) * 1000.0);
      if (t.width < 50) t.width = 50;
      if (t.period < t.width) t.period = t.width;
      t.count = atol(argv[4]);
      t.done = 0;
      t.level = 0;
      t.start = micros();
      o->pwm = 0;
      t.active = 1;
      break;
    }
    case 'X': {  // X pin
      DOut *o = findOut(a1);
      stopTrain(a1);
      if (o) writeOut(o, 0);
      break;
    }
    case 'A': {  // A channel period_ms deadband
      if (argc < 2 || nAn >= MAX_AN) { err("A: bad arguments or too many analogue inputs"); break; }
      AIn &a = ans[nAn++];
      a.pin = a1;
      a.period = argc > 2 && a2 > 0 ? a2 : 50;
      a.deadband = argc > 3 ? a3 : 2;
      a.last = analogRead(a.pin);
      a.next = millis() + a.period;
      reportAn(a);
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
    default:
      err("unknown command");
  }
}

void setup() {
  Serial.begin(115200);
  lastRx = millis();
}

void loop() {
  // serial commands
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (blen) {
        buf[blen] = 0;
        lastRx = millis();
        wdFired = false;
        command(buf);
        blen = 0;
      }
    } else if (blen < sizeof(buf) - 1) {
      buf[blen++] = ch;
    }
  }
  unsigned long now = millis();
  unsigned long us = micros();
  // pulse trains (microsecond timing, wrap-safe)
  for (uint8_t i = 0; i < MAX_TRAIN; i++) {
    Train &t = trains[i];
    if (!t.active) continue;
    DOut *o = findOut(t.pin);
    if (!o) { t.active = 0; continue; }
    unsigned long onT = t.start + t.done * t.period;
    if (!t.level) {
      if (t.count && t.done >= t.count) { t.active = 0; continue; }
      if ((long)(us - onT) >= 0) { writeOut(o, 1); t.level = 1; }
    } else if ((long)(us - (onT + t.width)) >= 0) {
      writeOut(o, 0);
      t.level = 0;
      t.done++;
    }
  }
  // outputs with a maximum on-time (e.g. shock safety cut-off)
  for (uint8_t i = 0; i < nOut; i++)
    if (outs[i].timed && outs[i].on && (long)(now - outs[i].offAt) >= 0) writeOut(&outs[i], 0);
  // debounced digital inputs
  for (uint8_t i = 0; i < nIn; i++) {
    DIn &d = ins[i];
    uint8_t raw = digitalRead(d.pin);
    if (raw != d.last) { d.last = raw; d.changed = now; }
    if (raw != d.stable && now - d.changed >= d.debounce) { d.stable = raw; reportIn(d); }
  }
  // analogue inputs
  for (uint8_t i = 0; i < nAn; i++) {
    AIn &a = ans[i];
    if ((long)(now - a.next) < 0) continue;
    a.next = now + a.period;
    int v = analogRead(a.pin);
    if (abs(v - a.last) > (int)a.deadband) { a.last = v; reportAn(a); }
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
