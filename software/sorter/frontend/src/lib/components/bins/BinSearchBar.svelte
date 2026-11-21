<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Button from '$lib/components/ui/Button.svelte';
	import Input from '$lib/components/ui/Input.svelte';

	let {
		query = $bindable(''),
		matchCount,
		totalBins
	}: {
		query?: string;
		matchCount: number | null;
		totalBins: number;
	} = $props();
</script>

<div class="flex flex-col gap-2">
	<Input
		bind:value={query}
		aria-label={$_('components.bins.binSearchBar.findBinAria')}
		placeholder={$_('components.bins.binSearchBar.findPlaceholder')}
		class="max-w-xl"
	>
		{#snippet end()}
			{#if query}
				<Button size="sm" variant="ghost" onclick={() => (query = '')}>
					{$_('components.bins.binSearchBar.clearButton')}
				</Button>
			{/if}
		{/snippet}
	</Input>
	{#if matchCount !== null}
		<p class="text-sm {matchCount === 0 ? 'text-warning-ink' : 'text-ink-muted'}">
			{matchCount === 0
				? $_('components.bins.binSearchBar.noMatch')
				: totalBins === 1
					? $_('components.bins.binSearchBar.matchSummarySingular', { values: { match: matchCount, total: totalBins } })
					: $_('components.bins.binSearchBar.matchSummaryPlural', { values: { match: matchCount, total: totalBins } })}
		</p>
	{/if}
</div>
