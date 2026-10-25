import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {checkWireConstants} from './check_hazard_frontend.mjs';

const current=await readFile(new URL('../web/src/contracts.ts',import.meta.url),'utf8');
test('current Linda/Cody constants match the physical output contract',()=>checkWireConstants(current));
test('a future frontend quantile change fails visibly',()=>assert.throws(()=>checkWireConstants(current.replace('[0.1, 0.3, 0.5, 0.7, 0.9]','[0.05, 0.25, 0.5, 0.75, 0.95]')),/QUANTILES changed/));
test('a future frontend lead-order change fails visibly',()=>assert.throws(()=>checkWireConstants(current.replace('[30, 60, 90','[60, 30, 90')),/LEADS_MIN changed/));
