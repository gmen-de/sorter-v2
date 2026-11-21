<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import { formatKind, type IncidentsSummary } from './incidents';

	// The incident numbers in one row: how many, how many are open, and which
	// kind is most frequent.
	let { summary }: { summary: IncidentsSummary } = $props();
</script>

<Panel flush>
	<div class="grid grid-cols-2 gap-px bg-line sm:grid-cols-4">
		<div class="bg-surface">
			<Stat label={$_('components.records.incidentTotals.totalLabel')} value={summary.total.toLocaleString()} />
		</div>
		<div class="bg-surface">
			<Stat
				label={$_('components.records.incidentTotals.activeLabel')}
				value={summary.active.toLocaleString()}
				hint={summary.active > 0 ? $_('components.records.incidentTotals.activeHint') : undefined}
				tone={summary.active > 0 ? 'warning' : undefined}
			/>
		</div>
		<div class="bg-surface">
			<Stat
				label={$_('components.records.incidentTotals.distinctKindsLabel')}
				value={summary.by_kind.length.toLocaleString()}
			/>
		</div>
		<div class="bg-surface">
			<Stat
				label={$_('components.records.incidentTotals.mostFrequentLabel')}
				value={summary.by_kind[0] ? formatKind(summary.by_kind[0].kind) : '—'}
				hint={summary.by_kind[0]
					? $_('components.records.incidentTotals.timesHint', { values: { count: summary.by_kind[0].count.toLocaleString() } })
					: undefined}
			/>
		</div>
	</div>
</Panel>
