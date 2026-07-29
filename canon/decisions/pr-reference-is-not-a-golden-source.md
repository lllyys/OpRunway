---
title: PR reference is not a golden source
updated: 2026-07-26
status: proposed
contradicts: [[ADR 0011 — Golden is decoupled from the engine and loaded per operator]]
---

# PR reference is not a golden source

用户 2026-07-22 裁定：PR 是被测物，PR 内的参考实现不得作为该 PR 的 golden 真值源，否则构成被测物
自证。实现上应让授权来源词表中不存在“PR reference”这一格，而不是仅靠一条可绕过的文字禁令。

仓内非 PR 的实现资料可以作为 `impl_reference` 帮助理解，但不因此获得 golden 授权。

**Sources.** [[session 8217ff1b-d287-4074-bfe1-a7d0bdb3809f · 2026-07-22]]
