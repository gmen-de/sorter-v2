<!-- A training run's colors, each a swatch and its name. -->
<script lang="ts">
	import { _ } from 'svelte-i18n';
	import ColorChip from '$lib/components/ui/ColorChip.svelte';
	import { findLegoColor } from '$lib/pieces/colors';
	import type { TrainingColor } from '$lib/training/api';

	let { colors, class: className = '' }: { colors: TrainingColor[]; class?: string } = $props();
</script>

<span class="inline-flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 {className}">
	{#each colors as color (color.id)}
		{@const known = findLegoColor(color.id, color.name)}
		<ColorChip
			name={color.name ??
				known?.name ??
				$_('components.training.runColors.colorFallback', { values: { id: color.id } })}
			rgb={color.rgb ?? known?.hex ?? null}
		/>
	{/each}
</span>
