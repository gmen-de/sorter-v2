<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';

	type LotRun = { id: string; name: string; adds_to_lot: boolean; started_at: number };
	type LotPart = {
		part_id: string;
		part_name: string | null;
		color_id: string | null;
		color_name: string | null;
		counts: Record<string, number>;
		in_lot: number;
	};

	let {
		open = $bindable(false),
		lot,
		endpointBase
	}: {
		open?: boolean;
		lot: { id: string; name: string } | null;
		endpointBase: string;
	} = $props();

	const PAGE = 100;

	let runs = $state<LotRun[]>([]);
	let parts = $state<LotPart[]>([]);
	let error = $state<string | null>(null);
	let loading = $state(false);
	let againOnly = $state(false);
	let shown = $state(PAGE);

	const seenAgain = $derived(parts.filter((p) => Object.keys(p.counts).length > 1));
	const rows = $derived((againOnly ? seenAgain : parts).slice(0, shown));
	const rowCount = $derived(againOnly ? seenAgain.length : parts.length);

	$effect(() => {
		if (open && lot) void load(lot.id);
	});

	async function load(lotId: string) {
		loading = true;
		error = null;
		shown = PAGE;
		try {
			const res = await fetch(`${endpointBase}/api/lots/${lotId}/parts`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const body = await res.json();
			runs = body.runs;
			parts = body.parts;
		} catch (e) {
			error = $_('components.records.lotPartsModal.loadError', {
				values: { error: e instanceof Error ? e.message : String(e) }
			});
		} finally {
			loading = false;
		}
	}
</script>

<Modal
	bind:open
	title={lot ? lot.name : $_('components.records.lotPartsModal.lotFallbackTitle')}
	size="lg"
	onclose={() => (open = false)}
>
	<div class="flex flex-col gap-3">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<p class="text-sm text-ink-muted">
				{$_('components.records.lotPartsModal.description')}
				{$_('components.records.lotPartsModal.cameThroughAgain', {
					values: { seen: seenAgain.length.toLocaleString(), total: parts.length.toLocaleString() }
				})}
			</p>
			<div class="flex items-center gap-2">
				<span id="lot-again-only" class="text-sm text-ink">
					{$_('components.records.lotPartsModal.onlySeenTwiceLabel')}
				</span>
				<Switch bind:checked={againOnly} labelledby="lot-again-only" />
			</div>
		</div>

		{#if error}
			<p class="text-sm text-danger-ink">{error}</p>
		{:else if loading}
			<p class="text-sm text-ink-muted">{$_('components.records.lotPartsModal.loadingLot')}</p>
		{:else}
			<div class="max-h-[60vh] overflow-auto">
				<table class="data-table">
					<thead>
						<tr>
							<th>{$_('components.records.lotPartsModal.partHeader')}</th>
							<th>{$_('components.records.lotPartsModal.colorHeader')}</th>
							{#each runs as run (run.id)}
								<th class="num">
									{run.name}
									<span class="block font-normal text-ink-muted">
										{run.adds_to_lot
											? $_('components.records.lotPartsModal.addsToLotLabel')
											: $_('components.records.lotPartsModal.anotherPassLabel')}
									</span>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each rows as part (`${part.part_id}-${part.color_id}`)}
							<tr>
								<td>
									{part.part_name ?? part.part_id}
									<span class="block text-ink-muted">{part.part_id}</span>
								</td>
								<td>{part.color_name ?? part.color_id ?? $_('components.records.lotPartsModal.unknownColor')}</td>
								{#each runs as run (run.id)}
									<td class="num {part.counts[run.id] ? '' : 'text-ink-faint'}">
										{part.counts[run.id] ?? 0}
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if shown < rowCount}
				<div class="flex justify-center">
					<Button size="sm" onclick={() => (shown += PAGE)}>
						{$_('components.records.lotPartsModal.showMore', {
							values: { count: Math.min(PAGE, rowCount - shown), remaining: (rowCount - shown).toLocaleString() }
						})}
					</Button>
				</div>
			{/if}
		{/if}
	</div>
</Modal>
