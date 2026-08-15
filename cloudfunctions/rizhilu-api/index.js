// 日知录记账 - CloudBase 云函数（数据中转 API）
// 访问路径：https://<env>.service.tcloudbase.com/cloudfunctions/rizhilu-api/<接口>
// event.path 为剥离路由前缀后的路径（如 /income、/stats/overview）
const cloudbase = require('@cloudbase/node-sdk');
const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV });
const db = app.database();

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
async function getAll(table) {
    const col = db.collection(table);
    let all = [], skip = 0, batch = 100;
    for (;;) {
        const res = await col.orderBy('date', 'desc').skip(skip).limit(batch).get();
        const docs = res.data || [];
        all = all.concat(docs.map(d => ({ ...d, id: d._id })));
        if (docs.length < batch) break;
        skip += batch;
        if (skip > 20000) break;
    }
    return all;
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
            const mode = body.mode === 'merge' ? 'merge' : 'replace';
            const imported = {};
            for (const [table, rows] of Object.entries(body.tables)) {
                if (!TABLES.includes(table)) continue;
                let count = 0;
                if (mode === 'replace') {
                    for (;;) {
                        const res = await db.collection(table).limit(100).get();
                        const docs = res.data || [];
                        if (!docs.length) break;
                        for (const d of docs) await db.collection(table).doc(d._id).remove();
                        if (docs.length < 100) break;
                    }
                }
                for (const row of rows) {
                    const c = clean(table, row);
                    // 合并模式：id 已存在则更新（导出的 id 即原 _id）；不存在则用原 id 创建，
                    // 保证跨设备/多次合并时同一记录 id 稳定，不会重复导入
                    if (mode === 'merge' && row.id) {
                        const ex = await db.collection(table).doc(String(row.id)).get();
                        if (ex.data && ex.data[0]) {
                            await db.collection(table).doc(String(row.id)).update(c);
                            count++;
                            continue;
                        }
                        await db.collection(table).doc(String(row.id)).set(c);
                        count++;
                        continue;
                    }
                    await db.collection(table).add(c);
                    count++;
                }
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
            const month = query.month || new Date().toISOString().slice(0, 7);
            if (type === 'overview') {
                const [incRows, expRows, finRows, elecRows] = await Promise.all([
                    getAll('income'), getAll('expense'), getAll('finance_snapshot'), getAll('electricity')
                ]);
                let ti = 0, te = 0, tl = 0, li = 0;
                incRows.forEach(r => {
                    if ((r.date || '').indexOf(month) !== 0) return;
                    const a = r.amount || 0; ti += a;
                    if (r.large === 1) li += a;
                });
                expRows.forEach(r => {
                    if ((r.date || '').indexOf(month) !== 0) return;
                    if (r.large === 1) tl += r.amount || 0; else te += r.amount || 0;
                });
                const latest_finance = finRows[0] ? { ...finRows[0], id: finRows[0]._id } : null;
                const latest_electricity = elecRows[0] ? { ...elecRows[0], id: elecRows[0]._id } : null;
                return ok({ month, total_income: Math.round(ti * 100) / 100, large_income: Math.round(li * 100) / 100, total_expense: Math.round(te * 100) / 100, large_expense: Math.round(tl * 100) / 100, balance: Math.round((ti - te - tl) * 100) / 100, latest_finance, latest_electricity });
            }
            if (type === 'expense' || type === 'income') {
                const rows = await getAll(type);
                const map = {};
                rows.forEach(r => {
                    if ((r.date || '').indexOf(month) !== 0) return;
                    const cat = r.category || '其他';
                    if (!map[cat]) map[cat] = { category: cat, total: 0, count: 0 };
                    map[cat].total += r.amount || 0;
                    map[cat].count += 1;
                });
                return ok(Object.values(map).sort((a, b) => b.total - a.total));
            }
            if (type === 'trend') {
                // 最近 6 个月收入/支出趋势
                const now = new Date();
                const months = [];
                for (let i = 5; i >= 0; i--) {
                    const dd = new Date(now.getFullYear(), now.getMonth() - i, 1);
                    months.push(dd.getFullYear() + '-' + String(dd.getMonth() + 1).padStart(2, '0'));
                }
                const [incRows, expRows] = await Promise.all([getAll('income'), getAll('expense')]);
                const sum = (rows, m) => rows.filter(r => (r.date || '').indexOf(m) === 0).reduce((s, r) => s + (r.amount || 0), 0);
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
        const rows = await getAll(table);
        if (query.month) return ok(rows.filter(r => (r.date || '').indexOf(query.month) === 0));
        return ok(rows);
    } catch (e) {
        console.error('API ERROR:', e);
        return fail(500, (e && e.message) || String(e));
    }
};
