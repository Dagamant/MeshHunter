/*
 * Seeed XIAO nRF52840 -- "raw" variant: Arduino pin N == nRF GPIO N
 * (P0.xx = xx, P1.xx = 32 + xx), so the sniffer firmware can name radio
 * pins by their real GPIO numbers instead of depending on a Dx mapping.
 *
 * XIAO pad -> GPIO:  D0=P0.02  D1=P0.03  D2=P0.28  D3=P0.29  D4=P0.04
 *                    D5=P0.05  D6=P1.11  D7=P1.12  D8=P1.13  D9=P1.14
 *                    D10=P1.15
 */
#ifndef _VARIANT_XIAO_NRF52840_RAW_
#define _VARIANT_XIAO_NRF52840_RAW_

#define VARIANT_MCK (64000000ul)
#define USE_LFRC

#include "WVariant.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PINS_COUNT         (48)
#define NUM_DIGITAL_PINS   (48)
#define NUM_ANALOG_INPUTS  (6)
#define NUM_ANALOG_OUTPUTS (0)

// On-board RGB LED, active low.
#define PIN_LED1     (26)  // red   P0.26
#define PIN_LED2     (6)   // blue  P0.06
#define PIN_LED3     (30)  // green P0.30
#define LED_RED      PIN_LED1
#define LED_BLUE     PIN_LED2
#define LED_GREEN    PIN_LED3
#define LED_BUILTIN  PIN_LED1
#define LED_CONN     PIN_LED2
#define LED_STATE_ON 0

#define PIN_A0 (2)
#define PIN_A1 (3)
#define PIN_A2 (28)
#define PIN_A3 (29)
#define PIN_A4 (4)
#define PIN_A5 (5)
#define PIN_A6 (0xff)
#define PIN_A7 (0xff)
static const uint8_t A0 = PIN_A0;
static const uint8_t A1 = PIN_A1;
static const uint8_t A2 = PIN_A2;
static const uint8_t A3 = PIN_A3;
static const uint8_t A4 = PIN_A4;
static const uint8_t A5 = PIN_A5;
static const uint8_t A6 = PIN_A6;
static const uint8_t A7 = PIN_A7;
#define ADC_RESOLUTION 14

#define PIN_AREF (0xff)
static const uint8_t AREF = PIN_AREF;

// D7 RX / D6 TX
#define PIN_SERIAL1_RX (44)
#define PIN_SERIAL1_TX (43)

#define SPI_INTERFACES_COUNT 1
#define PIN_SPI_SCK  (45)  // D8  P1.13
#define PIN_SPI_MISO (46)  // D9  P1.14
#define PIN_SPI_MOSI (47)  // D10 P1.15
static const uint8_t SS   = 4;
static const uint8_t MOSI = PIN_SPI_MOSI;
static const uint8_t MISO = PIN_SPI_MISO;
static const uint8_t SCK  = PIN_SPI_SCK;

#define WIRE_INTERFACES_COUNT 1
#define PIN_WIRE_SDA (4)
#define PIN_WIRE_SCL (5)

#ifdef __cplusplus
}
#endif

#endif
