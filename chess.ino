#include <Arduino.h>
#include <Wire.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <MD_MAX72xx.h>

#define HW_TYPE  MD_MAX72XX::FC16_HW
#define MAX_CS   10
#define NUM_DEV  1
MD_MAX72XX mx(HW_TYPE, MAX_CS, NUM_DEV);

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 oled(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1)

;
#define BTN_UP       2
#define BTN_DOWN     3
#define BTN_LEFT     4
#define BTN_RIGHT    5
#define BTN_CONFIRM  7
#define LED_CHECK    6

uint8_t board[8][8] = {
  {10,8,9,11,12,9,8,10},
  {7,7,7,7,7,7,7,7},
  {0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0},
  {1,1,1,1,1,1,1,1},
  {4,2,3,5,6,3,2,4}
};

const char* FILES = "abcdefgh";
const char* RANKS = "12345678";

int  cursorFile = 4, cursorRank = 1;
bool selectingFrom = true;
int  fromFile = -1, fromRank = -1;

unsigned long whiteTime = 600;
unsigned long blackTime = 600;
unsigned long lastClockUpdate = 0;
bool whiteTurn = true;

bool   inCheck = false;
String evalText = "+0.00";

static inline String squareToStr(int f, int r) {
  String s; s.reserve(2);
  s += FILES[f]; s += RANKS[r];
  return s;
}

static inline void waitRelease(int pin) {
  while (digitalRead(pin) == LOW) delay(10);
  delay(40);
}

static bool longPress(int pin, uint16_t ms = 1200) {
  if (digitalRead(pin) != LOW) return false;
  unsigned long t0 = millis();
  while (digitalRead(pin) == LOW) {
    if (millis() - t0 >= ms) { waitRelease(pin); return true; }
    delay(10);
  }
  delay(30);
  return false;
}

static void resetBoard() {
  const uint8_t startBoard[8][8] = {
    {10,8,9,11,12,9,8,10},
    {7,7,7,7,7,7,7,7},
    {0,0,0,0,0,0,0,0},
    {0,0,0,0,0,0,0,0},
    {0,0,0,0,0,0,0,0},
    {0,0,0,0,0,0,0,0},
    {1,1,1,1,1,1,1,1},
    {4,2,3,5,6,3,2,4}
  };
  memcpy(board, startBoard, sizeof(board));
  whiteTime = 600; blackTime = 600; whiteTurn = true;
  inCheck = false; digitalWrite(LED_CHECK, LOW);
}

static void displayBoard() {
  mx.clear();
  for (int r = 0; r < 8; r++) {
    for (int f = 0; f < 8; f++) {
      if (board[r][f] != 0) mx.setPoint(r, f, true);
    }
  }
  static unsigned long lastBlink = 0; static bool blinkState = false;
  if (millis() - lastBlink > 300) { blinkState = !blinkState; lastBlink = millis(); }
  if (blinkState) {
    bool cur = mx.getPoint(cursorRank, cursorFile);
    mx.setPoint(cursorRank, cursorFile, !cur);
  }
  mx.update();
}

static void formatTime(unsigned long seconds, char* out) {
  int m = seconds / 60, s = seconds % 60;
  sprintf(out, "%d:%02d", m, s);
}

static void drawOLED() {
  oled.clearDisplay();
  oled.setTextSize(1);
  oled.setTextColor(SSD1306_WHITE);

  oled.setCursor(0, 0);
  oled.print(evalText);

  char ts[10];
  oled.setCursor(84, 0);
  oled.print(whiteTurn ? "W>" : "W ");
  formatTime(whiteTime, ts); oled.print(ts);

  oled.setCursor(84, 10);
  oled.print(whiteTurn ? "B " : "B>");
  formatTime(blackTime, ts); oled.print(ts);

  if (inCheck) {
    oled.setCursor(110, 54);
    oled.print("CHK");
  }

  oled.display();
}

static inline void repaintOLED() { drawOLED(); }

static void updateClock() {
  if (millis() - lastClockUpdate >= 1000) {
    if (whiteTurn) { if (whiteTime > 0) whiteTime--; }
    else           { if (blackTime > 0) blackTime--; }
    lastClockUpdate = millis();
  }
}

static inline void sendMove(const String& from, const String& to) {
  Serial.print("MOVE:"); Serial.println(from + to);
}

static void handleSerial() {
  while (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (!line.length()) continue;

    if (line.startsWith("ENGINE:")) {
      int c1 = line.indexOf(':');
      int c2 = line.indexOf(':', c1 + 1);
      if (c1 > 0 && c2 > c1) {
        String uci = line.substring(c1 + 1, c2);
        if (uci.length() >= 4) {
          int ff = uci.charAt(0) - 'a';
          int fr = uci.charAt(1) - '1';
          int tf = uci.charAt(2) - 'a';
          int tr = uci.charAt(3) - '1';
          if (ff>=0 && ff<8 && fr>=0 && fr<8 && tf>=0 && tf<8 && tr>=0 && tr<8) {
            board[tr][tf] = board[fr][ff];
            board[fr][ff] = 0;
          }
          whiteTurn = !whiteTurn;
        }
      }
    } else if (line.startsWith("CHECK:")) {
      inCheck = (line.substring(6).toInt() == 1);
      digitalWrite(LED_CHECK, inCheck ? HIGH : LOW);
    } else if (line.startsWith("NEWGAME")) {
      resetBoard();
    } else if (line.startsWith("EVALTXT:")) {
      evalText = line.substring(8);
    }
  }
}

void setup() {
  pinMode(BTN_UP, INPUT_PULLUP);
  pinMode(BTN_DOWN, INPUT_PULLUP);
  pinMode(BTN_LEFT, INPUT_PULLUP);
  pinMode(BTN_RIGHT, INPUT_PULLUP);
  pinMode(BTN_CONFIRM, INPUT_PULLUP);
  pinMode(LED_CHECK, OUTPUT);
  digitalWrite(LED_CHECK, LOW);

  Wire.begin();
  oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  oled.clearDisplay(); oled.setTextColor(SSD1306_WHITE); oled.display();

  mx.begin();
  mx.control(MD_MAX72XX::INTENSITY, 5);
  mx.clear();

  Serial.begin(115200);
  Serial.println("READY");

  lastClockUpdate = millis();
  repaintOLED();
}

void loop() {
  bool changed = false;

  if (digitalRead(BTN_UP) == LOW)    { cursorRank = min(7, cursorRank + 1); changed=true; waitRelease(BTN_UP); }
  if (digitalRead(BTN_DOWN) == LOW)  { cursorRank = max(0, cursorRank - 1); changed=true; waitRelease(BTN_DOWN); }
  if (digitalRead(BTN_LEFT) == LOW)  { cursorFile = max(0, cursorFile - 1); changed=true; waitRelease(BTN_LEFT); }
  if (digitalRead(BTN_RIGHT) == LOW) { cursorFile = min(7, cursorFile + 1); changed=true; waitRelease(BTN_RIGHT); }

  if (digitalRead(BTN_CONFIRM) == LOW) {
    if (longPress(BTN_CONFIRM, 1200)) {
      Serial.println("NEWGAME");
      selectingFrom = true; fromFile = fromRank = -1; changed = true;
    } else {
      waitRelease(BTN_CONFIRM);
      if (selectingFrom) {
        if (board[cursorRank][cursorFile] != 0) {
          fromFile = cursorFile; fromRank = cursorRank;
          selectingFrom = false; changed = true;
        }
      } else {
        String from = squareToStr(fromFile, fromRank);
        String to   = squareToStr(cursorFile, cursorRank);
        board[cursorRank][cursorFile] = board[fromRank][fromFile];
        board[fromRank][fromFile] = 0;
        sendMove(from, to);
        selectingFrom = true; fromFile = fromRank = -1;
        whiteTurn = !whiteTurn; changed = true;
      }
    }
  }

  if (changed) repaintOLED();
  displayBoard();
  updateClock();
  handleSerial();

  static unsigned long lastRepaint = 0;
  if (millis() - lastRepaint > 200) { repaintOLED(); lastRepaint = millis(); }

  delay(40);
}
