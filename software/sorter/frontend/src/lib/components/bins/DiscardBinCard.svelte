<script lang="ts">
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import type { BinInfo, ExitBin, LayerInfo } from './types';

	let {
		layers,
		exitBin,
		saving = false,
		onSetExitBin,
		onClearExitBin
	}: {
		layers: LayerInfo[];
		exitBin: ExitBin | null;
		saving?: boolean;
		onSetExitBin: (layerIndex: number, sectionIndex: number, binIndex: number) => void;
		onClearExitBin: () => void;
	} = $props();

	// The picker the operator is editing, seeded from the configured exit bin
	// (or layer 0 / section 0 / bin 0 if none is set yet). Reseeded only when
	// the configured exit bin itself changes, so an in-progress pick survives
	// the periodic layout refresh.
	let pickLayer = $state(0);
	let pickSection = $state(0);
	let pickBin = $state(0);
	let seededFor: string | null = null;

	$effect(() => {
		const key = exitBin ? `${exitBin.layer_index}:${exitBin.section_index}:${exitBin.bin_index}` : 'none';
		if (key === seededFor) return;
		seededFor = key;
		pickLayer = exitBin?.layer_index ?? 0;
		pickSection = exitBin?.section_index ?? 0;
		pickBin = exitBin?.bin_index ?? 0;
	});

	function currentLayer(): LayerInfo | null {
		return layers.find((l) => l.layer_index === pickLayer) ?? null;
	}

	function binsInSection(sectionIndex: number): BinInfo[] {
		return (currentLayer()?.bins ?? []).filter((b) => b.section_index === sectionIndex);
	}

	function sectionIndexes(): number[] {
		const count = currentLayer()?.section_count ?? 0;
		return Array.from({ length: count }, (_, i) => i);
	}

	function pickValid(): boolean {
		const layer = currentLayer();
		if (!layer || pickSection >= (layer.section_count ?? 0)) return false;
		return binsInSection(pickSection).some((b) => b.bin_index === pickBin);
	}

	function isDirty(): boolean {
		if (!exitBin) return true;
		return (
			exitBin.layer_index !== pickLayer ||
			exitBin.section_index !== pickSection ||
			exitBin.bin_index !== pickBin
		);
	}
</script>

<!-- The passthrough destination for pieces with no real bin (no matching
     category, oversize, a double-feed bucket-hold, ...). On the original
     machine this is a virtual "bin": everything falls through the open
     bottom of the chute. Some builds (e.g. with a slip ring installed
     through the chute's center to power its free rotation) have no open
     bottom left, so an operator can redirect this passthrough to a real,
     physical bin instead - picked by layer/section/bin, same as any other
     bin address. -->
<Panel title="Discard / exit bin">
	{#snippet actions()}
		<Badge tone={exitBin ? 'primary' : 'neutral'}>
			{exitBin
				? `Layer ${exitBin.layer_index + 1} · Section ${exitBin.section_index + 1} · Bin ${exitBin.bin_index + 1}`
				: 'Virtual — falls through'}
		</Badge>
	{/snippet}
	<div class="flex flex-col gap-3">
		<p class="text-sm text-ink-muted">
			{exitBin
				? 'Pieces with no matching bin (unrecognized, oversized, or held back during a double-feed) are routed to the bin below instead of falling through the bottom.'
				: "Pieces with no matching bin (unrecognized, oversized, or held back during a double-feed) fall straight through every layer's open doors to the tray below the chute."}
		</p>
		<div class="flex flex-wrap items-end gap-3">
			<div class="w-32">
				<Field label="Layer" for="exit-bin-layer">
					<Select
						id="exit-bin-layer"
						value={String(pickLayer)}
						disabled={saving}
						onchange={(v) => {
							pickLayer = Number(v);
							pickSection = 0;
							pickBin = 0;
						}}
						options={layers.map((l) => ({
							value: String(l.layer_index),
							label: `Layer ${l.layer_index + 1}`
						}))}
					/>
				</Field>
			</div>
			<div class="w-32">
				<Field label="Section" for="exit-bin-section">
					<Select
						id="exit-bin-section"
						value={String(pickSection)}
						disabled={saving}
						onchange={(v) => {
							pickSection = Number(v);
							pickBin = 0;
						}}
						options={sectionIndexes().map((i) => ({
							value: String(i),
							label: `Section ${i + 1}`
						}))}
					/>
				</Field>
			</div>
			<div class="w-32">
				<Field label="Bin" for="exit-bin-bin">
					<Select
						id="exit-bin-bin"
						value={String(pickBin)}
						disabled={saving}
						onchange={(v) => (pickBin = Number(v))}
						options={binsInSection(pickSection).map((b) => ({
							value: String(b.bin_index),
							label: `Bin ${b.bin_index + 1}`
						}))}
					/>
				</Field>
			</div>
			<div class="ml-auto flex gap-2">
				{#if exitBin}
					<Button size="sm" disabled={saving} loading={saving} onclick={() => onClearExitBin()}>
						Clear (fall through bottom)
					</Button>
				{/if}
				<Button
					size="sm"
					variant="primary"
					disabled={saving || !pickValid() || !isDirty()}
					loading={saving}
					onclick={() => onSetExitBin(pickLayer, pickSection, pickBin)}
				>
					Use as exit bin
				</Button>
			</div>
		</div>
	</div>
</Panel>
