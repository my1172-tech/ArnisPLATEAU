# ArnisPLATEAU（アルニスプラトー）

GSI＋PLATEAU高さ＋LOD2屋根形状により、リアルな日本の街を生成するOSSアプリ

---

## 特徴

* PLATEAU APIから建物の実測高さ（measuredHeight）を自動取得
* PLATEAU LOD2 CityGMLによる屋根形状の再現
* Luanti・Bedrock・Java Edition の3形式に出力対応
* GUIアプリ（地図でエリア選択→ワンクリック生成）

---

## plateau2minecraft（国交省公式）との違い

| 項目    | plateau2minecraft | ArnisPLATEAU |
| ----- | ----------------- | ------------ |
| 操作    | CLIのみ             | GUIワンクリック    |
| 道路・地形 | なし                | あり           |
| データ準備 | CityGML手動DL       | 自動取得         |

---

## 使い方

1. ArnisPLATEAU.exe と arnis-jp.exe を同じフォルダに配置
2. ArnisPLATEAU.exe を起動
3. 地図上でエリアを選択
4. 設定でGSI・PLATEAUにチェック
5. 「生成開始」ボタンを押す

---

## ダウンロード

https://github.com/my1172-tech/ArnisPLATEAU/releases/latest

---

## クレジット・謝辞

本アプリは、オープンソースコミュニティおよび関連プロジェクトの成果に基づいて開発されています。関係者の皆様に感謝申し上げます。

### 特別謝辞

本プロジェクトの開発にあたり、musoukun 氏の記事および知見を参考にしています（掲載許可取得済み）。

* https://zenn.dev/musoukun/articles/d8c79a6b44d12c

---

## ベースソフトウェア

* arnis-jp
  https://github.com/musoukun/arnis-jp

* arnis
  https://github.com/louis-e/arnis

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

## 対応プラットフォーム

* Luanti / Mineclonia（LGPL v2.1）

---

## Modifications（本プロジェクトによる改変）

本プロジェクトでは以下の改変を実施しています。

* GUIアプリケーションの実装（エリア選択・生成UI）
* PLATEAUデータの自動取得機能の追加
* 建物高さ（measuredHeight）の反映処理
* LOD2屋根形状の再現処理
* 地理院地形・道路データの統合
* 出力形式（Luanti / Bedrock / Java）の拡張対応

---

## License

本プロジェクトは Apache License 2.0 のもとで公開されています。

### 本プロジェクトが依存するソフトウェア

* arnis
  https://github.com/louis-e/arnis
  Licensed under the Apache License 2.0

* arnis-jp
  https://github.com/musoukun/arnis-jp

---

## 注意事項

* 本アプリは各データ提供元の利用規約に従って使用してください
* PLATEAUデータ利用時は出典明記が必要です
* OpenStreetMapデータ利用時はODbLライセンスに従ってください

---

## 概要（教育・研究用途）

本ツールは、都市データの可視化および教育用途での活用（地理・情報・探究学習）を想定して開発されています。
