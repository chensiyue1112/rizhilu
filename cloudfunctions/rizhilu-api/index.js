// 日知录记账 - CloudBase 云函数（数据中转 API）
// 访问路径：https://<env>.service.tcloudbase.com/cloudfunctions/rizhilu-api/<接口>
// event.path 为剥离路由前缀后的路径（如 /income、/stats/overview）
const cloudbase = require('@cloudbase/node-sdk');
const app = cloudbase.init({ env: cloudbase.SYMBOL_CURRENT_ENV });
const db = app.database();

const TABLES = ['income', 'expense', 'finance_snapshot', 'electricity', 'mood', 'weather', 'record'];
const NUM_COLS = {
    income: ['amount'],
    expense: ['amount', 'large'],
    finance_snapshot: ['liquid_assets', 'debt', 'stock', 'fund', 'convertible_bond'],
    electricity: ['reading'],
    mood: ['rating'],
    weather: [],
    record: ['done'],
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

        // ---------- 导入（覆盖式） ----------
        if (path === '/import') {
            if (!body.tables) return fail(400, '无效的数据格式');
            const imported = {};
            for (const [table, rows] of Object.entries(body.tables)) {
                if (!TABLES.includes(table)) continue;
                for (;;) {
                    const res = await db.collection(table).limit(100).get();
                    const docs = res.data || [];
                    if (!docs.length) break;
                    for (const d of docs) await db.collection(table).doc(d._id).remove();
                    if (docs.length < 100) break;
                }
                for (const row of rows) await db.collection(table).add(clean(table, row));
                imported[table] = rows.length;
            }
            return ok({ message: '导入完成（覆盖式）', imported });
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
                let ti = 0, te = 0, tl = 0;
                incRows.forEach(r => { if ((r.date || '').indexOf(month) === 0) ti += r.amount || 0; });
                expRows.forEach(r => {
                    if ((r.date || '').indexOf(month) !== 0) return;
                    if (r.large === 1) tl += r.amount || 0; else te += r.amount || 0;
                });
                const latest_finance = finRows[0] ? { ...finRows[0], id: finRows[0]._id } : null;
                const latest_electricity = elecRows[0] ? { ...elecRows[0], id: elecRows[0]._id } : null;
                return ok({ month, total_income: Math.round(ti * 100) / 100, total_expense: Math.round(te * 100) / 100, large_expense: Math.round(tl * 100) / 100, balance: Math.round((ti - te - tl) * 100) / 100, latest_finance, latest_electricity });
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
            return fail(404, 'Unknown stats: ' + type);
        }

        // ---------- CRUD ----------
        const m = path.match(/^\/(\w+)(?:\/([^/]+))?$/);
        if (!m || !TABLES.includes(m[1])) return fail(404, 'Unknown API: ' + path);
        const table = m[1], id = m[2];

        if (method === 'DELETE') {
            await db.collection(table).doc(id).remove();
            return ok({ deleted: id });
        }
        if (method === 'PUT') {
            const c = clean(table, body);
            if (!Object.keys(c).length) return fail(400, '没有要更新的字段');
            await db.collection(table).doc(id).update(c);
            return ok({ _id: id, id, ...c });
        }
        if (method === 'POST') {
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
