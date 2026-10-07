import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadFixture } from './fixtures.js';
import { categoryDefaults, categoryLabel, ICON_CODE_POINTS, COLOUR_RGB } from '../../../build/generated/typescript/src/categoryRegistry.js';
import { CategorySystemKeyFromJSON } from '../../../build/generated/typescript/src/models/CategorySystemKey.js';
import { CategoryIconFromJSON } from '../../../build/generated/typescript/src/models/CategoryIcon.js';
import { CategoryColourFromJSON } from '../../../build/generated/typescript/src/models/CategoryColour.js';

interface Seed {
    categories: Array<{ key: string; parent_key: string | null; nature: string; icon: string; colour: string;
        labels: Record<string, string>; introduced_in: number; retired_in: number | null; sort_order: number }>;
    icons: Array<{ id: string; code_point: string }>;
    colours: Array<{ id: string; rgb: [number, number, number] }>;
}
test('typed category registries and every exact parent/nature/label default derive from the accepted source', () => {
    const seed = loadFixture<Seed>('../category-seed.v1.json');
    assert.equal(seed.categories.length, 59);
    for (const row of seed.categories) {
        const key = CategorySystemKeyFromJSON(row.key);
        const value = categoryDefaults(key);
        assert.equal(value.parentKey, row.parent_key);
        assert.equal(value.nature, row.nature);
        assert.equal(value.icon, row.icon);
        assert.equal(value.colour, row.colour);
        assert.equal(value.introducedIn, row.introduced_in);
        assert.equal(value.retiredIn, row.retired_in);
        assert.equal(value.sortOrder, row.sort_order);
        assert.equal(categoryLabel(key, 'en-IN'), row.labels['en-IN']);
        assert.equal(categoryLabel(key, 'hi-IN'), row.labels['en-IN']);
        assert.equal(Reflect.set(value, 'sortOrder', 99), false);
        assert.equal(Reflect.set(value.labels, 'en-IN', 'changed'), false);
    }
    assert.deepEqual(ICON_CODE_POINTS, Object.fromEntries(seed.icons.map((row) => [row.id, row.code_point])));
    assert.deepEqual(COLOUR_RGB, Object.fromEntries(seed.colours.map((row) => [row.id, row.rgb])));
    assert.ok(Object.isFrozen(ICON_CODE_POINTS));
    assert.ok(Object.isFrozen(COLOUR_RGB));
    for (const rgb of Object.values(COLOUR_RGB)) assert.equal(Reflect.set(rgb, '0', 999), false);
    for (const decode of [CategorySystemKeyFromJSON, CategoryIconFromJSON, CategoryColourFromJSON]) {
        for (const value of ['SYNTHETIC_UNKNOWN', 1, null]) assert.throws(() => decode(value));
    }
});
