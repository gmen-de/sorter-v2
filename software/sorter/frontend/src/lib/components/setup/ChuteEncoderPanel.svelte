<script lang="ts">
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';

	let {
		connected,
		encoderCount,
		indexSynced,
		homed,
		countsPerRevolution = $bindable(),
		homing,
		canceling,
		error,
		status,
		onFindIndex,
		onCancel,
		onSaveCountsPerRevolution
	}: {
		connected: boolean;
		encoderCount: number | null;
		indexSynced: boolean | null;
		homed: boolean | null;
		countsPerRevolution: number;
		homing: boolean;
		canceling: boolean;
		error: string | null;
		status: string | null;
		onFindIndex: () => void;
		onCancel: () => void;
		onSaveCountsPerRevolution: (value: number) => void;
	} = $props();
</script>

<Panel
	title="Chute magnet-ring encoder"
	description="This board has no mechanical endstop — the chute's position comes from a magnet-ring Hall encoder instead."
>
	{#snippet actions()}
		<Badge tone={connected ? 'success' : 'neutral'} dot>
			{connected ? 'Connected' : 'Not connected'}
		</Badge>
	{/snippet}

	<div class="flex flex-col gap-4">
		<Alert tone="warning">
			Not yet bench-verified against real hardware — see
			<code class="font-mono text-xs">software/firmware/octopus_control_board/ChuteEncoder.h</code>
			for the known caveats (direction sign, counts-per-revolution are a nominal guess).
		</Alert>

		<p class="text-sm text-ink-muted">
			"Find the index" rotates the chute slowly until the magnet ring's one index gap passes the
			sensors, then moves to bin 1 — the encoder equivalent of finding an endstop.
		</p>

		<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">Index synced</span>
				<Badge tone={indexSynced ? 'success' : 'neutral'}>
					{indexSynced === null ? '–' : indexSynced ? 'Yes' : 'Not yet'}
				</Badge>
			</div>
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">Homed</span>
				<Badge tone={homed ? 'success' : 'neutral'}>
					{homed === null ? '–' : homed ? 'Yes' : 'No'}
				</Badge>
			</div>
			<div class="flex items-center justify-between rounded-control bg-well px-4 py-3">
				<span class="text-sm text-ink-muted">Raw count</span>
				<span class="font-mono text-sm text-ink">{encoderCount ?? '–'}</span>
			</div>
		</div>

		<Field
			label="Counts per revolution"
			for="chute-encoder-counts-per-rev"
			help="Nominal guess, not bench-calibrated — adjust once real hardware confirms the real figure."
		>
			<Input
				id="chute-encoder-counts-per-rev"
				type="number"
				step={1}
				min={1}
				bind:value={countsPerRevolution}
				onchange={() => onSaveCountsPerRevolution(countsPerRevolution)}
			/>
		</Field>

		{#if error}
			<Alert tone="danger">{error}</Alert>
		{:else if status}
			<Alert tone="success">{status}</Alert>
		{/if}
	</div>

	{#snippet footer()}
		<Button variant="ghost" loading={canceling} onclick={onCancel}>Stop the motion</Button>
		<Button variant="primary" loading={homing} disabled={!connected} onclick={onFindIndex}>
			Find the index
		</Button>
	{/snippet}
</Panel>
