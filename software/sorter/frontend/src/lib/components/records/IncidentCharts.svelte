<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Panel from '$lib/components/ui/Panel.svelte';
	import BarList from './BarList.svelte';
	import SeriesChart from './SeriesChart.svelte';
	import { formatDuration, formatKind, type IncidentsSummary } from './incidents';

	// What the incidents are made of: per day, per channel, and per kind with
	// the time each takes to clear.
	let { summary }: { summary: IncidentsSummary } = $props();
</script>

<div class="grid grid-cols-1 gap-(--gap-panels) lg:grid-cols-2">
	<Panel
		title={$_('components.records.incidentCharts.perDayTitle')}
		description={$_('components.records.incidentCharts.lastYear')}
	>
		<SeriesChart
			points={summary.by_day.map((p) => ({ date: p.date, value: p.count }))}
			kind="bar"
			color="var(--danger)"
		/>
	</Panel>
	<Panel
		title={$_('components.records.incidentCharts.byChannelTitle')}
		description={$_('components.records.incidentCharts.allTime')}
	>
		<BarList
			empty={$_('components.records.incidentCharts.noIncidentsRecorded')}
			rows={summary.by_channel.map((c) => ({ key: c.channel, label: c.channel, count: c.count }))}
		/>
	</Panel>
	<Panel
		title={$_('components.records.incidentCharts.frequencyTitle')}
		description={$_('components.records.incidentCharts.frequencyDescription')}
		class="lg:col-span-2"
	>
		<BarList
			tone="danger"
			labelWidth="sm:w-40"
			empty={$_('components.records.incidentCharts.noIncidentsRecorded')}
			rows={summary.by_kind.map((k) => ({
				key: k.kind,
				label: formatKind(k.kind),
				count: k.count,
				note: $_('components.records.incidentCharts.avgSuffix', { values: { duration: formatDuration(k.avg_duration_s) } })
			}))}
		/>
	</Panel>
</div>
