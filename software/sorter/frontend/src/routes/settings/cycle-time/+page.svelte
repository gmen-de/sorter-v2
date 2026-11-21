<script lang="ts">
	/*
		Where each piece's seconds go on the classification channel (C4): its own
		work (confirming the landing, photos, waiting for the piece ahead to be
		classified and the chute aimed, the turn), and its waits for the feeder,
		split by where C3's next piece was when C4 asked. From the machine's
		piece cycles (/runtime-stats/cycles), one row per piece. Cycles that
		overlap an incident are counted apart: they are stops, not the flow.
	*/
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import { RUNTIME_SPANS, type RuntimeSpan } from '$lib/components/RuntimeStats.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';

	type Q = { n: number; median?: number; mean?: number; p90?: number; total?: number };
	type Case = Q & { key: string; label: string; share: number; c3_moving: number | null; chute: number | null };
	type Summary = {
		pieces: number;
		held: number;
		per_minute: number | null;
		per_minute_fed: number | null;
		multi_drop: number;
		c4: { confirm: Q; photos: Q; head_ready: Q; turn: Q; total: Q };
		wait: Q;
		cycle: Q;
		wait_cases: Case[];
		long_waits: { n: number; seconds: number; share: number };
	};
	type Wait = {
		id: number;
		asked_at: number;
		landed_at: number;
		c3_head_deg: number | null;
		c3_next_deg: number | null;
		c3_pieces: number | null;
		c2_pieces: number | null;
		c3_hidden: number | null;
		c3_moving_s: number | null;
		chute_s: number | null;
	};

	type Held = {
		id: number;
		channel: number;
		zone: string;
		started_at: number;
		ended_at: number;
		turns: number;
		gave_up: number;
		outcome: string;
	};

	const ctx = getMachineContext();
	let span = $state<RuntimeSpan>('1h');
	let summary = $state<Summary | null>(null);
	let waits = $state<Wait[]>([]);
	let held = $state<Held[]>([]);

	function since(s: RuntimeSpan): number {
		const now = Date.now() / 1000;
		if (s === '10m') return now - 600;
		if (s === '1h') return now - 3600;
		const midnight = new Date();
		midnight.setHours(0, 0, 0, 0);
		return midnight.getTime() / 1000;
	}

	async function load() {
		const base = machineHttpBaseUrlFromWsUrl(ctx.machine?.url) ?? getBackendHttpBase();
		const from = since(span);
		try {
			const [s, w, h] = await Promise.all([
				fetch(`${base}/runtime-stats/cycles?since=${from}`),
				fetch(`${base}/runtime-stats/cycles/waits?since=${from}&min_s=4&limit=40`),
				fetch(`${base}/runtime-stats/held-pieces?since=${from}`)
			]);
			if (s.ok) summary = await s.json();
			if (w.ok) waits = (await w.json()).waits ?? [];
			if (h.ok) held = (await h.json()).held ?? [];
		} catch {
			// The next poll tries again.
		}
	}

	$effect(() => {
		void span;
		void ctx.machine?.url;
		load();
		const id = setInterval(load, 10000);
		return () => clearInterval(id);
	});

	const secs = (v: number | undefined | null) => (v == null ? '—' : `${v.toFixed(1)} s`);
	const pct = (v: number | undefined | null) => (v == null ? '—' : `${Math.round(v * 100)}%`);
	const clock = (t: number) =>
		new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }).toLowerCase();
	function where(w: Wait): string {
		if (w.c3_head_deg != null) {
			return $_('routes.settings.cycleTime.longestWaits.where.degFromEdge', {
				values: { deg: Math.max(0, Math.round(w.c3_head_deg)) }
			});
		}
		if (w.c3_hidden) return $_('routes.settings.cycleTime.longestWaits.where.onlyOutOfView');
		return w.c2_pieces
			? $_('routes.settings.cycleTime.longestWaits.where.noneOnC3SomeOnC2')
			: $_('routes.settings.cycleTime.longestWaits.where.noneOnC3OrC2');
	}
	const OUTCOME = $derived<Record<string, string>>({
		freed: $_('routes.settings.cycleTime.heldPieces.outcomes.freed'),
		gone: $_('routes.settings.cycleTime.heldPieces.outcomes.gone'),
		stopped: $_('routes.settings.cycleTime.heldPieces.outcomes.stopped')
	});
	function fedHint(s: Summary): string {
		const pieces = s.held
			? $_('routes.settings.cycleTime.fedHint.piecesWithHeld', { values: { count: s.pieces, held: s.held } })
			: $_('routes.settings.cycleTime.fedHint.pieces', { values: { count: s.pieces } });
		if (s.per_minute_fed == null || s.per_minute == null || s.per_minute_fed - s.per_minute < 0.2) return pieces;
		return $_('routes.settings.cycleTime.fedHint.keptUp', { values: { value: s.per_minute_fed.toFixed(1) } });
	}

	const phases = $derived(
		summary
			? [
					{ label: $_('routes.settings.cycleTime.phases.confirm'), q: summary.c4.confirm },
					{ label: $_('routes.settings.cycleTime.phases.photos'), q: summary.c4.photos },
					{ label: $_('routes.settings.cycleTime.phases.headReady'), q: summary.c4.head_ready },
					{ label: $_('routes.settings.cycleTime.phases.turn'), q: summary.c4.turn },
					{ label: $_('routes.settings.cycleTime.phases.total'), q: summary.c4.total }
				]
			: []
	);
</script>

<svelte:head><title>{$_('routes.settings.cycleTime.pageTitle')}</title></svelte:head>

<PageHeader title={$_('routes.settings.cycleTime.title')} description={$_('routes.settings.cycleTime.description')}>
	{#snippet actions()}
		<SegmentedControl
			bind:value={span}
			options={RUNTIME_SPANS}
			label={$_('routes.settings.cycleTime.timeSpanLabel')}
			size="sm"
		/>
	{/snippet}
</PageHeader>

<div class="flex flex-col gap-4">
	<Panel flush>
		<div class="grid grid-cols-2 gap-px bg-line md:grid-cols-4">
			<div class="bg-surface">
				<Stat
					label={$_('routes.settings.cycleTime.stats.piecesPerMinute')}
					value={summary?.per_minute != null ? summary.per_minute.toFixed(1) : '—'}
					hint={summary ? fedHint(summary) : undefined}
				/>
			</div>
			<div class="bg-surface">
				<Stat
					label={$_('routes.settings.cycleTime.stats.perPiece')}
					value={secs(summary?.cycle.mean)}
					hint={$_('routes.settings.cycleTime.stats.medianHint', { values: { value: secs(summary?.cycle.median) } })}
				/>
			</div>
			<div class="bg-surface">
				<Stat
					label={$_('routes.settings.cycleTime.stats.c4Work')}
					value={secs(summary?.c4.total.mean)}
					hint={$_('routes.settings.cycleTime.stats.medianHint', { values: { value: secs(summary?.c4.total.median) } })}
				/>
			</div>
			<div class="bg-surface">
				<Stat
					label={$_('routes.settings.cycleTime.stats.waitingForFeeder')}
					value={secs(summary?.wait.mean)}
					hint={summary
						? $_('routes.settings.cycleTime.stats.waitingHint', {
								values: { median: secs(summary.wait.median), pct: pct(summary.long_waits.share) }
							})
						: undefined}
				/>
			</div>
		</div>
	</Panel>

	<Panel
		title={$_('routes.settings.cycleTime.c4Panel.title')}
		description={$_('routes.settings.cycleTime.c4Panel.description')}
		flush
	>
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr>
						<th>{$_('routes.settings.cycleTime.c4Panel.columns.phase')}</th>
						<th class="num">{$_('routes.settings.cycleTime.c4Panel.columns.median')}</th>
						<th class="num">{$_('routes.settings.cycleTime.c4Panel.columns.average')}</th>
						<th class="num">{$_('routes.settings.cycleTime.c4Panel.columns.slowest')}</th>
					</tr>
				</thead>
				<tbody>
					{#each phases as p (p.label)}
						<tr>
							<td>{p.label}</td>
							<td class="num">{secs(p.q.median)}</td>
							<td class="num">{secs(p.q.mean)}</td>
							<td class="num text-ink-muted">{secs(p.q.p90)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Panel>

	<Panel
		title={$_('routes.settings.cycleTime.waitPanel.title')}
		description={$_('routes.settings.cycleTime.waitPanel.description')}
		flush
	>
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr>
						<th>{$_('routes.settings.cycleTime.waitPanel.columns.when')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.asks')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.median')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.average')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.totalSeconds')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.c3Moving')}</th>
						<th class="num">{$_('routes.settings.cycleTime.waitPanel.columns.chuteMoving')}</th>
					</tr>
				</thead>
				<tbody>
					{#each summary?.wait_cases ?? [] as c (c.key)}
						<tr>
							<td>{c.label}</td>
							<td class="num">{c.n} <span class="text-ink-muted">({pct(c.share)})</span></td>
							<td class="num">{secs(c.median)}</td>
							<td class="num">{secs(c.mean)}</td>
							<td class="num">{c.total != null ? Math.round(c.total) : '—'}</td>
							<td class="num text-ink-muted">{pct(c.c3_moving)}</td>
							<td class="num text-ink-muted">{pct(c.chute)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Panel>

	<Panel
		title={$_('routes.settings.cycleTime.longestWaits.title')}
		description={$_('routes.settings.cycleTime.longestWaits.description')}
		flush
	>
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr>
						<th>{$_('routes.settings.cycleTime.longestWaits.columns.when')}</th>
						<th class="num">{$_('routes.settings.cycleTime.longestWaits.columns.wait')}</th>
						<th>{$_('routes.settings.cycleTime.longestWaits.columns.c3NextPiece')}</th>
						<th class="num">{$_('routes.settings.cycleTime.longestWaits.columns.onC3')}</th>
						<th class="num">{$_('routes.settings.cycleTime.longestWaits.columns.onC2')}</th>
						<th class="num">{$_('routes.settings.cycleTime.longestWaits.columns.c3Moving')}</th>
						<th class="num">{$_('routes.settings.cycleTime.longestWaits.columns.chuteMoving')}</th>
					</tr>
				</thead>
				<tbody>
					{#if waits.length === 0}
						<tr>
							<td class="text-center text-ink-muted" colspan="7"
								>{$_('routes.settings.cycleTime.longestWaits.empty')}</td
							>
						</tr>
					{:else}
						{#each waits as w (w.id)}
							{@const wait = w.landed_at - w.asked_at}
							<tr>
								<td class="whitespace-nowrap text-ink-muted">{clock(w.asked_at)}</td>
								<td class="num">{secs(wait)}</td>
								<td>{where(w)}</td>
								<td class="num">{w.c3_pieces ?? '—'}</td>
								<td class="num">{w.c2_pieces ?? '—'}</td>
								<td class="num text-ink-muted">{pct((w.c3_moving_s ?? 0) / wait)}</td>
								<td class="num text-ink-muted">{pct((w.chute_s ?? 0) / wait)}</td>
							</tr>
						{/each}
					{/if}
				</tbody>
			</table>
		</div>
	</Panel>

	<Panel
		title={$_('routes.settings.cycleTime.heldPieces.title')}
		description={$_('routes.settings.cycleTime.heldPieces.description')}
		flush
	>
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr>
						<th>{$_('routes.settings.cycleTime.heldPieces.columns.when')}</th>
						<th>{$_('routes.settings.cycleTime.heldPieces.columns.where')}</th>
						<th class="num">{$_('routes.settings.cycleTime.heldPieces.columns.heldFor')}</th>
						<th class="num">{$_('routes.settings.cycleTime.heldPieces.columns.turns')}</th>
						<th>{$_('routes.settings.cycleTime.heldPieces.columns.outcome')}</th>
					</tr>
				</thead>
				<tbody>
					{#if held.length === 0}
						<tr>
							<td class="text-center text-ink-muted" colspan="5"
								>{$_('routes.settings.cycleTime.heldPieces.empty')}</td
							>
						</tr>
					{:else}
						{#each held as h (h.id)}
							<tr>
								<td class="whitespace-nowrap text-ink-muted">{clock(h.started_at)}</td>
								<td
									>C{h.channel}, {h.zone === 'landing'
										? $_('routes.settings.cycleTime.heldPieces.landingArea')
										: $_('routes.settings.cycleTime.heldPieces.exit')}</td
								>
								<td class="num">{secs(h.ended_at - h.started_at)}</td>
								<td class="num">{h.turns}</td>
								<td
									>{OUTCOME[h.outcome] ?? h.outcome}{h.gave_up
										? $_('routes.settings.cycleTime.heldPieces.gaveUpSuffix')
										: ''}</td
								>
							</tr>
						{/each}
					{/if}
				</tbody>
			</table>
		</div>
	</Panel>
</div>
