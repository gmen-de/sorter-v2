<script lang="ts">
	import { _ } from 'svelte-i18n';
	import ServoLayerCalibrator from '$lib/components/servo/ServoLayerCalibrator.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.storageLayers.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.storageLayers.title')}
	description={$_('routes.settings.storageLayers.description')}
/>
<ServoLayerCalibrator />
