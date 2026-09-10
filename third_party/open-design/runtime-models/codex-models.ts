// Extracted unchanged parser functions from Open Design defs/codex.ts.
import {DEFAULT_MODEL_OPTION} from './models.js';
import type {RuntimeModelOption} from './types.js';
function parseCodexStringList(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const values = raw
    .map((value) => (typeof value === 'string' ? value.trim() : ''))
    .filter(Boolean);
  return values.length > 0 ? values : undefined;
}

function parseCodexServiceTiers(raw: unknown): RuntimeModelOption[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: RuntimeModelOption[] = [];
  const seen = new Set<string>();
  for (const tier of raw) {
    if (!tier || typeof tier !== 'object') continue;
    const entry = tier as {
      id?: unknown;
      name?: unknown;
      label?: unknown;
    };
    const id = typeof entry.id === 'string' ? entry.id.trim() : '';
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label =
      typeof entry.name === 'string' && entry.name.trim()
        ? entry.name.trim()
        : typeof entry.label === 'string' && entry.label.trim()
          ? entry.label.trim()
        : id;
    out.push({ id, label });
  }
  return out.length > 0 ? out : undefined;
}

const CODEX_SPEED_TIER_SERVICE_TIER_OPTIONS: Record<string, RuntimeModelOption> = {
  fast: { id: 'priority', label: 'Fast' },
};

function parseCodexServiceTiersFromSpeedTiers(
  speedTiers: readonly string[] | undefined,
): RuntimeModelOption[] | undefined {
  if (!speedTiers) return undefined;
  const out: RuntimeModelOption[] = [];
  const seen = new Set<string>();
  for (const raw of speedTiers) {
    const option = CODEX_SPEED_TIER_SERVICE_TIER_OPTIONS[raw.toLowerCase()];
    if (!option || seen.has(option.id)) continue;
    seen.add(option.id);
    out.push({ ...option });
  }
  return out.length > 0 ? out : undefined;
}

export function parseCodexDebugModels(stdout: string): RuntimeModelOption[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(String(stdout || ''));
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const models = Array.isArray(parsed)
    ? parsed
    : (parsed as { models?: unknown }).models;
  if (!Array.isArray(models)) return null;

  const out = [DEFAULT_MODEL_OPTION];
  const seen = new Set<string>([DEFAULT_MODEL_OPTION.id]);
  for (const raw of models) {
    if (!raw || typeof raw !== 'object') continue;
    const entry = raw as {
      slug?: unknown;
      id?: unknown;
      display_name?: unknown;
      name?: unknown;
      visibility?: unknown;
      additional_speed_tiers?: unknown;
      service_tiers?: unknown;
    };
    if (entry.visibility === 'hidden') continue;
    const id =
      typeof entry.slug === 'string'
        ? entry.slug.trim()
        : typeof entry.id === 'string'
          ? entry.id.trim()
          : '';
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const label =
      typeof entry.display_name === 'string' && entry.display_name.trim()
        ? entry.display_name.trim()
        : typeof entry.name === 'string' && entry.name.trim()
          ? entry.name.trim()
          : id;
    const model: RuntimeModelOption = { id, label };
    const additionalSpeedTiers = parseCodexStringList(
      entry.additional_speed_tiers,
    );
    if (additionalSpeedTiers) model.additionalSpeedTiers = additionalSpeedTiers;
    const serviceTierOptions =
      parseCodexServiceTiers(entry.service_tiers) ??
      parseCodexServiceTiersFromSpeedTiers(additionalSpeedTiers);
    if (serviceTierOptions) model.serviceTierOptions = serviceTierOptions;
    out.push(model);
  }
  return out.length > 1 ? out : null;
}

