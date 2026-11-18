/*
 *******************************************************************************
 * Copyright (c) 2020-2021, STMicroelectronics
 * All rights reserved.
 *
 * This software component is licensed by ST under BSD 3-Clause license,
 * the "License"; You may not use this file except in compliance with the
 * License. You may obtain a copy of the License at:
 *                        opensource.org/licenses/BSD-3-Clause
 *
 *******************************************************************************
 */
#if defined(ARDUINO_GENERIC_F446ZCHX) || defined(ARDUINO_GENERIC_F446ZCJX) ||\
    defined(ARDUINO_GENERIC_F446ZCTX) || defined(ARDUINO_GENERIC_F446ZEHX) ||\
    defined(ARDUINO_GENERIC_F446ZEJX) || defined(ARDUINO_GENERIC_F446ZETX)
#include "pins_arduino.h"

/**
  * @brief  System Clock Configuration
  * @param  None
  * @retval None
  */
WEAK void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {};
  RCC_PeriphCLKInitTypeDef PeriphClkInitStruct = {};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  *
  * This board has a real 12MHz HSE crystal (confirmed: the F446 variant of
  * the Octopus takes 12MHz, vs. 8MHz on the F429/F407 variants) — not HSI.
  * USB full-speed needs the 48MHz clock within +-0.25%; HSI's internal RC
  * oscillator (~+-1%) isn't accurate enough, which is why USB-CDC never
  * enumerated on the unmodified generic/Nucleo clock config this file was
  * cloned from (Nucleo boards run their MCU off HSI/ST-LINK MCO and don't
  * need this distinction). See HSE_VALUE=12000000 in octopus_f446.json's
  * extra_flags, which must stay in sync with PLLM/PLLSAIM below.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 6; // VCO input = 12MHz/6 = 2MHz (was HSE_VALUE/PLLM with HSI=16MHz/8)
  RCC_OscInitStruct.PLL.PLLN = 180;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 8;
  RCC_OscInitStruct.PLL.PLLR = 2;
  // On a genuine cold power-on the 12MHz crystal starts from a fully
  // de-energized state and can be slower/less reliable to stabilize than
  // on a warm reset (DFU-triggered reboot, watchdog reset, ...), where the
  // crystal was already oscillating and this call returns almost
  // instantly - bench-observed 2026-11-16: boots fine after every DFU
  // reflash, never comes up (no USB enumeration at all) after a true
  // power cycle. HSE_STARTUP_TIMEOUT is raised (see octopus_f446.json) to
  // give a single attempt more time, and this retries a few times with a
  // full HSE off/on toggle in between in case the oscillator just needs a
  // fresh kick rather than more patience within one attempt.
  {
    HAL_StatusTypeDef osc_status = HAL_ERROR;
    for (int attempt = 0; attempt < 5; attempt++) {
      osc_status = HAL_RCC_OscConfig(&RCC_OscInitStruct);
      if (osc_status == HAL_OK) break;
      RCC_OscInitTypeDef hse_off = {};
      hse_off.OscillatorType = RCC_OSCILLATORTYPE_HSE;
      hse_off.HSEState = RCC_HSE_OFF;
      HAL_RCC_OscConfig(&hse_off);
      HAL_Delay(50);
    }
    if (osc_status != HAL_OK) {
      Error_Handler();
    }
  }
  /** Activate the Over-Drive mode
  */
  if (HAL_PWREx_EnableOverDrive() != HAL_OK) {
    Error_Handler();
  }
  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                                | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLRCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK) {
    Error_Handler();
  }
  PeriphClkInitStruct.PeriphClockSelection = RCC_PERIPHCLK_SDIO | RCC_PERIPHCLK_CLK48;
  // PLLSAI has its own, independent M divider on the F446 (unlike plain F4
  // parts, confirmed in stm32f4xx_hal_rcc_ex.c's F446-specific branch) — so
  // this doesn't need to match the main PLL's PLLM above, only the VCO-input
  // range (1-2MHz) and the CLK48 result. VCO input = 12MHz/6 = 2MHz,
  // VCO output = 2MHz*96 = 192MHz, CLK48 = 192MHz/4 (PLLSAIP) = 48MHz exactly.
  PeriphClkInitStruct.PLLSAI.PLLSAIM = 6;
  PeriphClkInitStruct.PLLSAI.PLLSAIN = 96;
  PeriphClkInitStruct.PLLSAI.PLLSAIQ = 2;
  PeriphClkInitStruct.PLLSAI.PLLSAIP = RCC_PLLSAIP_DIV4;
  PeriphClkInitStruct.PLLSAIDivQ = 1;
  PeriphClkInitStruct.Clk48ClockSelection = RCC_CLK48CLKSOURCE_PLLSAIP;
  PeriphClkInitStruct.SdioClockSelection = RCC_SDIOCLKSOURCE_CLK48;
  if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInitStruct) != HAL_OK) {
    Error_Handler();
  }
}

#endif /* ARDUINO_GENERIC_* */
