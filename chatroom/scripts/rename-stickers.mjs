// 貼圖批次重命名工具：把 stickers/ 內雜亂檔名，依「加入時間(mtime)」升序
// 重命名成有序的 001.<ext>、002.<ext>…（保留原副檔名、轉小寫、補零 3 位）。
// 用法：在 chatroom/ 目錄執行 `npm run stickers:rename`
// 安全：兩階段改名（先 .rename-tmp-*，再正式名）避免 002→001 覆蓋既有 001；可重複執行。

import { readdir, stat, rename } from 'node:fs/promises';
import { join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const STICKERS_DIR = fileURLToPath(new URL('../stickers', import.meta.url));
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp']);
const PAD = 3; // 001..999

const isImage = (name) => IMAGE_EXTS.has(extname(name).toLowerCase());
const pad = (n) => String(n).padStart(PAD, '0');

const main = async () => {
  let entries;
  try {
    entries = await readdir(STICKERS_DIR);
  } catch {
    console.error(`找不到貼圖資料夾：${STICKERS_DIR}`);
    process.exit(1);
  }

  // 先清掉上次中斷殘留的暫存檔（盡力還原），再重新讀取
  const stale = entries.filter((name) => name.startsWith('.rename-tmp-'));
  for (const name of stale) {
    const restored = name.replace('.rename-tmp-', '');
    await rename(join(STICKERS_DIR, name), join(STICKERS_DIR, restored)).catch(() => {});
  }
  if (stale.length > 0) entries = await readdir(STICKERS_DIR);

  const images = entries.filter(isImage);
  if (images.length === 0) {
    console.log('stickers/ 內沒有圖片（.png/.jpg/.jpeg/.gif/.webp），無需重命名。');
    return;
  }

  // 依 mtime 升序（先加入的排前面），同時間以檔名穩定排序
  const withTime = await Promise.all(
    images.map(async (name) => ({ name, mtime: (await stat(join(STICKERS_DIR, name))).mtimeMs })),
  );
  withTime.sort((a, b) => a.mtime - b.mtime || a.name.localeCompare(b.name));

  const plan = withTime.map((item, i) => ({
    from: item.name,
    to: `${pad(i + 1)}${extname(item.name).toLowerCase()}`,
  }));

  // 已完全符合目標 → 冪等跳過
  if (plan.every((p) => p.from === p.to)) {
    console.log(`已是有序命名（${plan.length} 張），無需變更。`);
    return;
  }

  // 兩階段：先全部改暫存名，再改正式名，避免目標名與既有檔碰撞
  const staged = plan.map((p, i) => ({
    ...p,
    tmp: `.rename-tmp-${pad(i + 1)}${extname(p.from).toLowerCase()}`,
  }));
  for (const p of staged) {
    await rename(join(STICKERS_DIR, p.from), join(STICKERS_DIR, p.tmp));
  }
  for (const p of staged) {
    await rename(join(STICKERS_DIR, p.tmp), join(STICKERS_DIR, p.to));
  }

  console.log(`已重命名 ${plan.length} 張（依加入時間排序）：`);
  for (const p of plan) {
    if (p.from !== p.to) console.log(`  ${p.from}  ->  ${p.to}`);
  }
};

main().catch((err) => {
  console.error('重命名失敗：', err);
  process.exit(1);
});
