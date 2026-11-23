<!--
	A one-line "what is this channel doing right now" strip for the Dashboard,
	fed by the same runtime_stats.state_machines the backend already keeps for
	distribution/classification — see backend/subsystems/feeder/pulse_perception/
	flow.py's _publishChannelStatus() for where feeder_ch2/feeder_ch3 states
	come from, and two_piece/flow.py's phaseName() for classification's.
	current_state IS the "waiting on what" answer: a FREEZE'd feeder channel
	publishes the specific thing it's blocked on (e.g. "waiting_for_classification"),
	not just "waiting".
-->
<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import Badge from '$lib/components/ui/Badge.svelte';

	type Tone = 'neutral' | 'primary' | 'info' | 'success' | 'warning' | 'danger';

	let {
		stateMachines,
		machineKey
	}: {
		stateMachines: Record<string, { current_state?: string; entered_at?: number }> | null | undefined;
		machineKey: string;
	} = $props();

	// Feeder channels (C2/C3) and the classification channel use different
	// state vocabularies -- see flow.py's _publishChannelStatus() and
	// two_piece/flow.py's _Phase/phaseName() respectively.
	const STATUS_TONE: Record<string, Tone> = {
		empty: 'neutral',
		idle: 'neutral',
		disabled: 'neutral',
		advancing: 'info',
		exiting: 'success',
		waiting_for_classification: 'warning',
		waiting_for_handoff_gate: 'warning',
		waiting_for_c3_drop_clear: 'warning',
		waiting_for_c3_room: 'warning',
		waiting_for_downstream: 'warning',
		piece_held_recovery: 'danger',
		waiting_for_piece: 'neutral',
		waiting: 'info',
		ejecting: 'success',
		staging: 'info'
	};

	function statusLabel(raw: string): string {
		const key = `components.channelStatusBar.status.${raw}`;
		const translated = $_(key);
		// svelte-i18n returns the key itself when nothing matches -- fall back to
		// a humanized version of the raw backend state name instead of showing
		// the raw dotted key path.
		if (translated === key) return raw.replace(/_/g, ' ');
		return translated;
	}

	const entry = $derived.by(() => {
		const raw = stateMachines?.[machineKey]?.current_state;
		if (!raw) return null;
		return { raw, tone: STATUS_TONE[raw] ?? 'neutral' };
	});

	// Ticks once a second so "for Ns" keeps counting without waiting on the
	// next runtime_stats push (which only arrives when the state itself changes).
	let now = $state(Date.now());
	let timer: ReturnType<typeof setInterval> | undefined;
	onMount(() => {
		timer = setInterval(() => (now = Date.now()), 1000);
	});
	onDestroy(() => {
		if (timer) clearInterval(timer);
	});

	function sinceLabel(): string | null {
		const enteredAt = stateMachines?.[machineKey]?.entered_at;
		if (!enteredAt) return null;
		const seconds = Math.max(0, Math.round(now / 1000 - enteredAt));
		if (seconds < 60) return $_('components.channelStatusBar.forSeconds', { values: { s: seconds } });
		const minutes = Math.floor(seconds / 60);
		const rem = seconds % 60;
		return $_('components.channelStatusBar.forMinutes', { values: { m: minutes, s: rem } });
	}
</script>

{#if entry}
	{@const since = sinceLabel()}
	<div class="flex items-center gap-2 rounded-control bg-well px-3 py-1.5 text-sm">
		<Badge tone={entry.tone} dot>{statusLabel(entry.raw)}</Badge>
		{#if since}
			<span class="text-ink-muted">{since}</span>
		{/if}
	</div>
{:else}
	<div class="flex items-center gap-2 rounded-control bg-well px-3 py-1.5 text-sm text-ink-faint">
		{$_('components.channelStatusBar.noData')}
	</div>
{/if}
