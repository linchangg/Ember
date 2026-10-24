// G1-b criterion 4 check (spec/04 §1) using the same client SDK a React page uses.
// npx tsx spike/subscribe.ts [seconds]   (reads OPERATOR_PIN from engine/.env)
import { DbConnection, tables } from './bindings';

process.loadEnvFile(new URL('../../engine/.env', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
const URI = 'ws://localhost:3000';
const RUN_S = Number(process.argv[2] ?? 60);
const ts = () => new Date().toISOString().slice(11, 23);

const conn = DbConnection.builder()
  .withUri(URI)
  .withDatabaseName('emberline')
  .onConnect((c, identity) => {
    console.log(`${ts()} connected as ${identity.toHexString().slice(0, 8)}…`);
    c.subscriptionBuilder()
      .onApplied(() => {
        console.log(`${ts()} subscription applied: ${c.db.cycle.count()} cycle rows`);
        console.log(`${ts()} calling clock_play`);
        c.reducers
          .clockPlay({ scenarioId: 'fixture', pin: process.env.OPERATOR_PIN ?? '' })
          .then(() => console.log(`${ts()} clock_play committed`))
          .catch(e => console.error(`${ts()} clock_play failed:`, e));
      })
      .subscribe('SELECT * FROM cycle');
  })
  .onConnectError((_ctx, err) => {
    console.error('connect error', err);
    process.exit(1);
  })
  .build();

conn.db.cycle.onInsert((_ctx, row) =>
  console.log(`${ts()} cycle insert ${row.cycleId} k=${row.knowledgeSimMs} burned_ha=${row.burnedHa}`)
);
conn.db.cycle.onUpdate((_ctx, _old, row) =>
  console.log(`${ts()} cycle update ${row.cycleId} burned_ha=${row.burnedHa}`)
);

setTimeout(() => {
  conn.disconnect();
  process.exit(0);
}, RUN_S * 1000);
