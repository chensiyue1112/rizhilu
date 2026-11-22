// 日知录记账 - CloudBase 云函数（数据中转 API）
// 访问路径：https://<env>.service.tcloudbase.com/cloudfunctions/rizhilu-api/<接口>
// event.path 为剥离路由前缀后的路径（如 /income、/stats/overview）
const cloudbase = require('@cloudbase/node-sdk');
const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV });
const db = app.database();
const _ = db.command;

const TABLES = ['income', 'expense', 'finance_snapshot', 'electricity', 'mood', 'weather', 'record'];
const NUM_COLS = {
    income: ['amount', 'large'],
    expense: ['amount', 'large'],
    finance_snapshot: ['liquid_assets', 'debt', 'stock', 'fund', 'convertible_bond'],
    electricity: ['reading'],
    mood: ['rating'],
    weather: [],
    record: ['done'],
};
const REQUIRED = {
    income: ['date', 'amount'],
    expense: ['date', 'amount', 'category'],
    finance_snapshot: ['date'],
    electricity: ['date', 'reading'],
    mood: ['date'],
    weather: ['date'],
    record: ['content'],
};

function ok(data) {
    return { statusCode: 200, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify(data) };
}
function fail(status, msg) {
    return { statusCode: status, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify({ error: msg }) };
}
function clean(table, obj) {
    const o = {};
    for (const [k, v] of Object.entries(obj)) {
        if (k === 'id' || k === '_id') continue;
        o[k] = NUM_COLS[table] && NUM_COLS[table].includes(k) ? (parseFloat(v) || 0) : v;
    }
    return o;
}
function pad2(n) { return String(n).padStart(2, '0'); }

// CloudBase 运行时通常是 UTC；统一按北京时间取“今天/本月”，
// 避免北京时间午夜后到 UTC 午夜前这段被算到上一个月/上一天。
function nowPartsCN() {
    const d = new Date(Date.now() + 8 * 60 * 60 * 1000);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 };
}
function nowMonthCN() {
    const p = nowPartsCN();
    return p.y + '-' + pad2(p.m);
}
// '2026-11' → 2026-11-01 ~ 2026-12-01（左闭右开，字符串日期可直接比较）
function monthRange(month) {
    const [y, m] = month.split('-').map(Number);
    const ny = m === 12 ? y + 1 : y;
    const nm = m === 12 ? 1 : m + 1;
    return { start: month + '-01', end: ny + '-' + pad2(nm) + '-01' };
}
function validMonth(v) { return /^\d{4}-(0[1-9]|1[0-2])$/.test(v || ''); }

// 按月/起始日期在数据库侧过滤，避免把整张表读出来再在内存里筛选。
// 记账数据会持续增长，全表扫描会越来越慢，也白白消耗读次数。
async function getAll(table, filter) {
    filter = filter || {};
    let cond = null;
    if (filter.month) {
        const r = monthRange(filter.month);
        cond = { date: _.gte(r.start).and(_.lt(r.end)) };
    } else if (filter.since) {
        cond = { date: _.gte(filter.since) };
    }
    const col = db.collection(table);
    let all = [], skip = 0, batch = 100;
    for (;;) {
        // 日期倒序；同日期内按时间倒序（新的在前），收支表才有 time 字段
        let q = cond ? col.where(cond) : col;
        q = q.orderBy('date', 'desc');
        if (table === 'income' || table === 'expense') q = q.orderBy('time', 'desc');
        const res = await q.skip(skip).limit(batch).get();
        const docs = res.data || [];
        all = all.concat(docs.map(d => ({ ...d, id: d._id })));
        if (docs.length < batch) break;
        skip += batch;
        if (skip > 20000) break;
    }
    return all;
}

// 只取最新一条（财务快照 / 电表），不再为了拿第一行而读全表
async function getLatest(table) {
    const res = await db.collection(table).orderBy('date', 'desc').limit(1).get();
    const d = res.data && res.data[0];
    return d ? { ...d, id: d._id } : null;
}

// 限量并发：导入上千条记录时串行一条条写会超过云函数 10 秒超时
async function forEachLimit(items, limit, worker) {
    let next = 0;
    const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
        for (;;) {
            const i = next++;
            if (i >= items.length) return;
            await worker(items[i], i);
        }
    });
    await Promise.all(workers);
}

exports.main = async (event) => {
    try {
        const path = event.path || '';
        const method = (event.httpMethod || 'GET').toUpperCase();
        const query = event.queryStringParameters || {};
        let body = {};
        try { body = event.body ? JSON.parse(event.body) : {}; } catch (e) { body = {}; }

        // 处理浏览器 CORS 预检（跨域 POST/PUT/DELETE 需要）
        if (method === 'OPTIONS') {
            return {
                statusCode: 204,
                headers: {
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Methods': 'GET,HEAD,PUT,POST,DELETE,PATCH,OPTIONS',
                    'Access-Control-Allow-Headers': 'Content-Type,Authorization,X-Requested-With',
                    'Access-Control-Max-Age': '86400'
                },
                body: ''
            };
        }

        // ---------- 导入（replace 覆盖式 / merge 合并式） ----------
        if (path === '/import') {
            if (!body.tables) return fail(400, '无效的数据格式');
            // 前端把模式放在 query（/import?mode=merge），导出的 JSON 里没有 mode。
            // 以前只读 body.mode，「合并」实际执行成了「替换」，会把现有数据清空。
            const mode = (query.mode || body.mode) === 'merge' ? 'merge' : 'replace';
            const imported = {};
            for (const [table, rows] of Object.entries(body.tables)) {
                if (!TABLES.includes(table) || !Array.isArray(rows)) continue;
                let count = 0;
                if (mode === 'replace') {
                    // 每 100 条批量删除，避免上千条记录逐条 remove 超时
                    for (;;) {
                        const res = await db.collection(table).limit(100).get();
                        const docs = res.data || [];
                        if (!docs.length) break;
                        await db.collection(table).where({ _id: _.in(docs.map(d => d._id)) }).remove();
                        if (docs.length < 100) break;
                    }
                }
                // 并发写入（8 路），比一条一条 await 快一个数量级
                await forEachLimit(rows, 8, async (row) => {
                    const c = clean(table, row);
                    // 合并模式：id 已存在则更新（导出的 id 即原 _id）；不存在则用原 id 创建，
                    // 保证跨设备/多次合并时同一记录 id 稳定，不会重复导入
                    if (mode === 'merge' && row.id) {
                        const doc = db.collection(table).doc(String(row.id));
                        const ex = await doc.get();
                        if (ex.data && ex.data[0]) await doc.update(c);
                        else await doc.set(c);
                        count++;
                        return;
                    }
                    await db.collection(table).add(c);
                    count++;
                });
                imported[table] = count;
            }
            return ok({ message: '导入完成（模式: ' + mode + '）', imported });
        }

        // ---------- 导出 ----------
        if (path === '/export') {
            const pkg = { version: 1, exported_at: new Date().toLocaleString('zh-CN'), tables: {} };
            for (const t of TABLES) {
                const rows = await getAll(t);
                pkg.tables[t] = rows.map(r => {
                    const o = { id: r.id };
                    for (const c of Object.keys(r)) if (c !== '_id') o[c] = r[c];
                    return o;
                });
            }
            return ok(pkg);
        }

        // ---------- 统计 ----------
        const statsMatch = path.match(/^\/stats\/(\w+)/);
        if (statsMatch) {
            const type = statsMatch[1];
            const month = validMonth(query.month) ? query.month : nowMonthCN();
            if (type === 'overview') {
                const [incRows, expRows, latest_finance, latest_electricity] = await Promise.all([
                    getAll('income', { month }),
                    getAll('expense', { month }),
                    getLatest('finance_snapshot'),
                    getLatest('electricity')
                ]);
                let ti = 0, te = 0, tl = 0, li = 0;
                incRows.forEach(r => {
                    const a = Number(r.amount) || 0; ti += a;
                    if (Number(r.large) === 1) li += a;   // 工资收入（旧数据可能是字符串 "1"）
                });
                expRows.forEach(r => {
                    const a = Number(r.amount) || 0;
                    // 大额支出 = 单笔支出 > 100（自动判定）
                    if (a > 100) tl += a; else te += a;
                });
                const side = ti - li;                     // 副业收入 = 总收入 - 工资收入
                return ok({ month, total_income: Math.round(ti * 100) / 100, side_income: Math.round(side * 100) / 100, large_income: Math.round(li * 100) / 100, total_expense: Math.round(te * 100) / 100, large_expense: Math.round(tl * 100) / 100, balance: Math.round((ti - te - tl) * 100) / 100, latest_finance, latest_electricity });
            }
            if (type === 'expense' || type === 'income') {
                const rows = await getAll(type, { month });
                const map = {};
                rows.forEach(r => {
                    const cat = r.category || '其他';
                    if (!map[cat]) map[cat] = { category: cat, total: 0, count: 0 };
                    map[cat].total += Number(r.amount) || 0;
                    map[cat].count += 1;
                });
                return ok(Object.values(map).sort((a, b) => b.total - a.total));
            }
            if (type === 'trend') {
                // 最近 6 个月收入/支出趋势（按北京时间算月份）
                const p = nowPartsCN();
                const months = [];
                for (let i = 5; i >= 0; i--) {
                    const dd = new Date(p.y, p.m - 1 - i, 1);
                    months.push(dd.getFullYear() + '-' + pad2(dd.getMonth() + 1));
                }
                // 只读这 6 个月的记录，不再全表读出来逐月过滤
                const [incRows, expRows] = await Promise.all([
                    getAll('income', { since: months[0] + '-01' }),
                    getAll('expense', { since: months[0] + '-01' })
                ]);
                const sum = (rows, m) => rows.filter(r => String(r.date || '').indexOf(m) === 0).reduce((s, r) => s + (Number(r.amount) || 0), 0);
                return ok({ months, inc: months.map(m => Math.round(sum(incRows, m) * 100) / 100), exp: months.map(m => Math.round(sum(expRows, m) * 100) / 100) });
            }
            return fail(404, 'Unknown stats: ' + type);
        }

        // ---------- CRUD ----------
        const m = path.match(/^\/(\w+)(?:\/([^/]+))?$/);
        if (!m || !TABLES.includes(m[1])) return fail(404, 'Unknown API: ' + path);
        const table = m[1], id = m[2];

        if (method === 'DELETE') {
            const ex = await db.collection(table).doc(id).get();
            if (!(ex.data && ex.data[0])) return fail(404, 'not found');
            await db.collection(table).doc(id).remove();
            return ok({ deleted: id });
        }
        if (method === 'PUT') {
            const c = clean(table, body);
            if (!Object.keys(c).length) return fail(400, '没有要更新的字段');
            const ex = await db.collection(table).doc(id).get();
            if (!(ex.data && ex.data[0])) return fail(404, 'not found');
            await db.collection(table).doc(id).update(c);
            return ok({ id, ...c });
        }
        if (method === 'POST') {
            const missing = (REQUIRED[table] || []).filter(k => body[k] === undefined || body[k] === null || body[k] === '');
            if (missing.length) return fail(400, '缺少必填字段: ' + missing.join(', '));
            const c = clean(table, body);
            const res = await db.collection(table).add(c);
            return ok({ _id: res.id, id: res.id, ...c });
        }
        // GET
        if (id) {
            const res = await db.collection(table).doc(id).get();
            const d = res.data && res.data[0];
            if (!d) return fail(404, 'not found');
            return ok({ ...d, id: d._id });
        }
        // 按月查询直接在数据库里过滤（前端列表/统计大量使用 ?month=）
        if (query.month && !validMonth(query.month)) return fail(400, 'month 格式应为 YYYY-MM');
        const rows = await getAll(table, query.month ? { month: query.month } : {});
        return ok(rows);
    } catch (e) {
        console.error('API ERROR:', e);
        return fail(500, (e && e.message) || String(e));
    }
};
