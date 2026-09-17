import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  CONTRACT_ENUMS as BASE_ENUMS,
  LIQUIDITY_CONTRACT_ENUMS,
  NO_WICK_CONTRACT_ENUMS,
  SESSION_CONTRACT_ENUMS,
  SETUP_CONTRACT_ENUMS,
  ENTRY_CONTRACT_ENUMS,
  RISK_CONTRACT_ENUMS,
  ALERT_CONTRACT_ENUMS,
  ASSISTANT_CONTRACT_ENUMS,
  NEWS_CONTRACT_ENUMS,
  MACRO_CONTRACT_ENUMS,
  JOURNAL_CONTRACT_ENUMS,
  PAPER_CONTRACT_ENUMS,
  ANALYTICS_CONTRACT_ENUMS,
  BACKTEST_CONTRACT_ENUMS,
  REPLAY_CONTRACT_ENUMS,
  PD_ARRAY_CONTRACT_ENUMS,
  STRUCTURE_CONTRACT_ENUMS,
} from "../src/index";

const CONTRACT_ENUMS = {
  ...BASE_ENUMS,
  ...STRUCTURE_CONTRACT_ENUMS,
  ...LIQUIDITY_CONTRACT_ENUMS,
  ...PD_ARRAY_CONTRACT_ENUMS,
  ...NO_WICK_CONTRACT_ENUMS,
  ...SESSION_CONTRACT_ENUMS,
  ...SETUP_CONTRACT_ENUMS,
  ...ENTRY_CONTRACT_ENUMS,
  ...RISK_CONTRACT_ENUMS,
  ...ALERT_CONTRACT_ENUMS,
  ...ASSISTANT_CONTRACT_ENUMS,
  ...NEWS_CONTRACT_ENUMS,
  ...MACRO_CONTRACT_ENUMS,
  ...JOURNAL_CONTRACT_ENUMS,
  ...PAPER_CONTRACT_ENUMS,
  ...ANALYTICS_CONTRACT_ENUMS,
  ...BACKTEST_CONTRACT_ENUMS,
  ...REPLAY_CONTRACT_ENUMS,
};

const here = dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(
  readFileSync(resolve(here, "../../strategy-spec/enums.json"), "utf-8"),
) as Record<string, unknown>;

describe("shared-types enum contract", () => {
  it("declares exactly the enum sets in strategy-spec", () => {
    const specNames = Object.keys(spec).filter((k) => !k.startsWith("$")).sort();
    expect(Object.keys(CONTRACT_ENUMS).sort()).toEqual(specNames);
  });

  for (const [name, values] of Object.entries(CONTRACT_ENUMS)) {
    it(`${name} values and order match strategy-spec`, () => {
      expect([...values]).toEqual(spec[name]);
    });
  }
});
