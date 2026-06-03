import { WebSocket } from 'ws';

const URL = `ws://localhost:${process.env.PORT || 3999}`;
let pass = 0, fail = 0;
const ok = (name, cond) => { cond ? (pass++, console.log(`  ✅ ${name}`)) : (fail++, console.log(`  ❌ ${name}`)); };

const open = () => new Promise((res) => { const w = new WebSocket(URL); w.on('open', () => res(w)); });
const next = (w) => new Promise((res) => w.once('message', (d) => res(JSON.parse(d.toString()))));
// 讀到指定 type 為止，跳過中間其他事件（避免佇列污染）
const nextType = (w, type) => new Promise((res) => {
  const on = (d) => { const m = JSON.parse(d.toString()); if (m.type === type) { w.off('message', on); res(m); } };
  w.on('message', on);
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const enter = (w, code, name) => w.send(JSON.stringify({ type: 'enter', code, name }));

const run = async () => {
  // 1. 建房：回傳 6 位房號 + 自己的暱稱
  const a = await open();
  a.send(JSON.stringify({ type: 'create', name: 'Alice' }));
  const created = await next(a);
  ok('建房回傳房號 + 暱稱', created.type === 'room_created' && created.code.length === 6 && created.self === 'Alice');
  const code = created.code;

  // 2 & 3. 加入：加入者收 joined（含成員名單），既有成員收 peer_joined（含新成員名）
  const b = await open();
  const aPeerJoined = next(a);
  enter(b, code, 'Bob');
  const bJoined = await next(b);
  ok('加入者收到 joined + 成員名單', bJoined.type === 'joined' && bJoined.members.includes('Alice') && bJoined.members.includes('Bob'));
  const pj = await aPeerJoined;
  ok('既有成員收到 peer_joined + 新成員名', pj.type === 'peer_joined' && pj.name === 'Bob');

  // 4 & 5. 訊息轉發（帶發送者名）+ 無自我回聲
  const bRecv = next(b);
  let aEcho = false;
  a.once('message', () => { aEcho = true; });
  a.send(JSON.stringify({ type: 'message', text: 'hello' }));
  const msg = await bRecv;
  ok('訊息轉發帶發送者名', msg.type === 'message' && msg.text === 'hello' && msg.name === 'Alice');
  await sleep(100);
  ok('無自我回聲（發送者收不到自己訊息）', aEcho === false);

  // 6. 多人廣播：第三人加入後，A 發言 B 與 C 都收到
  const c = await open();
  const bPeerJoined = next(b); // C 加入時 B 會先收到 peer_joined，先消化掉
  enter(c, code, 'Carol');
  await next(c); // c 的 joined
  await bPeerJoined; // 清掉 b 佇列的 peer_joined
  const bRecv2 = next(b);
  const cRecv = next(c);
  a.send(JSON.stringify({ type: 'message', text: 'hi all' }));
  const [m2, m3] = await Promise.all([bRecv2, cRecv]);
  ok('多人廣播：房內其他人都收到', m2.text === 'hi all' && m3.text === 'hi all' && m2.name === 'Alice');

  // 7. typing 帶發送者名
  const bTyping = next(b);
  a.send(JSON.stringify({ type: 'typing', isTyping: true }));
  const typing = await bTyping;
  ok('typing 帶發送者名', typing.type === 'typing' && typing.name === 'Alice' && typing.isTyping === true);

  // 8. 自訂容量：建房設 capacity=3，回傳容量 + 房主身分，第 4 人被拒
  const host = await open();
  host.send(JSON.stringify({ type: 'create', name: 'H', capacity: 3 }));
  const cap = await next(host);
  ok('建房回傳自訂容量 + isHost', cap.capacity === 3 && cap.isHost === true);
  const fillers = [];
  for (let i = 0; i < 2; i++) {
    const s = await open();
    enter(s, cap.code, `U${i}`);
    await next(s); // joined（host + 2 = 3 人）
    fillers.push(s);
  }
  const fourth = await open();
  enter(fourth, cap.code, 'Late');
  const full = await next(fourth);
  ok('滿 3 人（自訂容量）後第 4 人被拒', full.type === 'error' && full.reason === 'room_full');
  fourth.close(); host.close(); fillers.forEach((s) => s.close());

  // 8b. 加入者 isHost = false
  const ih = await open();
  ih.send(JSON.stringify({ type: 'create', name: 'Owner', capacity: 5 }));
  const ihRoom = await next(ih);
  const guest = await open();
  enter(guest, ihRoom.code, 'Guest');
  const guestJoined = await next(guest);
  ok('加入者 isHost = false', guestJoined.isHost === false);

  // 9. 鎖房：房主鎖定後新人被拒 room_locked
  const lockP = nextType(ih, 'lock_changed');
  ih.send(JSON.stringify({ type: 'set_lock', locked: true }));
  const lockEcho = await lockP;
  ok('房主收到 lock_changed 廣播', lockEcho.locked === true);
  const blocked = await open();
  enter(blocked, ihRoom.code, 'Blocked');
  const lockedErr = await next(blocked);
  ok('鎖房後新人被拒（room_locked）', lockedErr.type === 'error' && lockedErr.reason === 'room_locked');
  blocked.close(); guest.close(); ih.close();

  // 9. 固定房號：不存在則自動建房，第二人進同號 → joined
  const d = await open();
  enter(d, 'FIXED1', 'Dee');
  const fixedCreated = await next(d);
  ok('固定房號不存在 → 自動建房', fixedCreated.type === 'room_created' && fixedCreated.code === 'FIXED1');
  const e = await open();
  enter(e, 'FIXED1', 'Eve');
  const eJoined = await next(e);
  ok('第二人進固定房號 → joined', eJoined.type === 'joined' && eJoined.members.includes('Dee'));
  d.close(); e.close();

  // 10. 非法房號
  const f = await open();
  enter(f, 'abc', 'X');
  const invalid = await next(f);
  ok('非法房號被拒（invalid_code）', invalid.type === 'error' && invalid.reason === 'invalid_code');
  f.close();

  // 11. 斷線通知帶離開者名字
  const bLeft = nextType(b, 'peer_left'); // a 是房主，離開會先觸發 host_changed
  a.close();
  const left = await bLeft;
  ok('斷線收到 peer_left + 離開者名', left.name === 'Alice');
  b.close(); c.close();

  console.log(`\n結果：${pass} 通過 / ${fail} 失敗`);
  process.exit(fail ? 1 : 0);
};

run().catch((e) => { console.error(e); process.exit(1); });
