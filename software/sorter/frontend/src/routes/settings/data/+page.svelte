<script lang="ts">
	import { _ } from 'svelte-i18n';
	import LocalDataPanel from '$lib/components/data/LocalDataPanel.svelte';
	import SharingPanel from '$lib/components/data/SharingPanel.svelte';
	import SampleCaptureSection from '$lib/components/settings/SampleCaptureSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
</script>

<svelte:head><title>{$_('routes.settings.data.index.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.data.index.title')}
	description={$_('routes.settings.data.index.description')}
/>

<SharingPanel />

<Panel
	title={$_('routes.settings.data.index.sampleCapture.title')}
	description={$_('routes.settings.data.index.sampleCapture.description')}
	flush
>
	<SampleCaptureSection />
</Panel>

<LocalDataPanel />
