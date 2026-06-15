# ArnisPLATEAU

GSI＋PLATEAU高さ＋LOD2屋根形状でリアルな日本の街を生成するOSSアプリ

---

## 特徴

* PLATEAU APIから建物の実測高さ（measuredHeight）を自動取得
* PLATEAU LOD2 CityGMLによる屋根形状の再現
* Luanti・Bedrock・Java Edition 全3形式で出力対応
* GUIアプリ（地図でエリア選択→ワンクリック生成）

---

## plateau2minecraft（国交省公式）との違い

| 項目    | plateau2minecraft | ArnisPLATEAU |
| ----- | ----------------- | ------------ |
| 操作    | CLIのみ             | GUIワンクリック    |
| 道路・地形 | なし                | あり           |
| データ準備 | CityGML手動DL       | 自動取得         |
| 商用利用  | Minecraft前提       | Luanti版は完全可  |

---

## 使い方

1. ArnisPLATEAU.exe と arnis-jp.exe を同じフォルダに配置
2. ArnisPLATEAU.exe を起動
3. 地図上でエリアを選択
4. 設定でGSI・PLATEAUにチェック
5. 生成開始ボタンを押す

---

## ダウンロード

https://github.com/my1172-tech/ArnisPLATEAU/releases/tag/v0.1.0

---

## クレジット・謝辞

本アプリは、オープンソースコミュニティおよび関連プロジェクトの成果に基づいて開発されています。関係者の皆様に感謝申し上げます。

### 技術的参考

* https://zenn.dev/musoukun/articles/d8c79a6b44d12c （musoukun）

---

## ベースソフトウェア

* arnis-jp
  https://github.com/musoukun/arnis-jp
  Copyright (c) musoukun

* arnis
  https://github.com/louis-e/arnis
  Copyright (c) louis-e

---

## 使用データ・ライセンス

* PLATEAU
  © 国土交通省 Project PLATEAU
  https://www.mlit.go.jp/plateau/
  Licensed under CC BY 4.0

* 国土地理院（GSI）
  https://www.gsi.go.jp/
  Licensed under CC BY 4.0

* OpenStreetMap
  © OpenStreetMap contributors
  https://www.openstreetmap.org/
  Licensed under ODbL

---

## 対応プラットフォームライセンス

* Luanti / Mineclonia
  Licensed under LGPL v2.1

---

## Modifications（本プロジェクトによる改変）

This project includes the following modifications:

* GUIアプリケーションの実装（エリア選択・生成UI）
* PLATEAUデータの自動取得機能の追加
* 建物高さ（measuredHeight）の反映処理
* LOD2屋根形状の再現処理
* GSI地形・道路データ統合処理
* 出力形式（Luanti / Bedrock / Java）の拡張対応

---

## License

This project is licensed under the Apache License 2.0.

This project is based on:

* arnis
  Licensed under the Apache License 2.0
  https://github.com/louis-e/arnis

* arnis-jp
  https://github.com/musoukun/arnis-jp

---

## 注意事項

* 本アプリは各データ提供元の利用規約に従って使用してください
* PLATEAUデータ利用時は出典明記が必要です
* OpenStreetMapデータ利用時はODbLライセンスに従ってください
