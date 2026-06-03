# 自社仕様：ワイヤレス充電器（コイル位置フィードバック付き）
# Target Specification: Wireless Charger with Coil-Alignment Feedback

## 概要 / Overview

本製品は、受信コイルの位置を検出し、送電効率を最大化するように送信コイルの
駆動信号を自動調整するコンシューマ向けワイヤレス充電システムである。

This product is a consumer wireless charging system that detects receiver coil
position and automatically adjusts the transmitter coil drive signal to maximize
power transfer efficiency.

## 機能要件 / Functional Requirements

- **送信コイル (Transmitter Coil)**: 直径 50 mm の平面型送信コイルを内蔵し、
  13.56 MHz の共振周波数で動作する。The device includes a planar transmitter coil
  operating at 13.56 MHz resonance frequency.

- **位置センサ (Position Sensor)**: 磁界センサのアレイを用いて受信コイルの位置を
  検出する。Position detection uses an array of magnetic field sensors to detect
  the receiver coil position with sub-millimeter accuracy.

- **コントローラ (Controller)**: マイクロコントローラが位置センサの出力に基づいて
  駆動信号の周波数および電力を調整する。A controller adjusts the drive signal
  frequency and power level based on the position sensor output.

- **異物検出 (Foreign Object Detection / FOD)**: 金属異物を検出した場合は充電を
  即座に停止する。The system detects metallic foreign objects and immediately
  halts charging to prevent overheating.

- **整合フィードバック (Alignment Feedback)**: コイル整合状態をリアルタイムで
  監視し、ユーザ向け LED インジケータに整合品質を表示する。Alignment feedback
  monitors coil alignment in real time and indicates alignment quality via an LED
  indicator.

- **電力調整 (Power Regulation)**: 受信デバイスの充電状態に応じて供給電力を
  5 W から 15 W の範囲で動的に調整する。Power regulation dynamically adjusts
  supplied power from 5 W to 15 W based on the receiving device charge state.

- **熱管理 (Thermal Management)**: 送信コイルの温度をサーミスタで監視し、
  60 °C を超えた場合は電力を制限する。A thermistor monitors transmitter coil
  temperature and limits power if the temperature exceeds 60 degrees C.

- **通信インターフェース (Communication Interface)**: Qi 規格 v1.3 準拠の
  バックスキャッタ変調による送受信間デジタル通信をサポートする。The device
  supports Qi 1.3 digital communication between transmitter and receiver via
  backscatter modulation.

- **筐体 (Housing)**: IP54 防塵・防水等級の筐体に内蔵し、落下衝撃試験
  IEC 60068-2-31 に適合する。The product housing meets IP54 dust and water
  resistance and complies with IEC 60068-2-31 drop impact testing.

- **効率 (Efficiency)**: 最大電力転送効率 85 % 以上（コイル完全整合時）。
  End-to-end power transfer efficiency of at least 85 percent at full coil alignment.
