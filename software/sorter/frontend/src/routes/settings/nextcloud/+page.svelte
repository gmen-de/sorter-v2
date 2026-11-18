<script lang="ts">
	import NextcloudSection from '$lib/components/settings/NextcloudSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>Sorter - Nextcloud</title></svelte:head>

<PageHeader
	title="Nextcloud"
	description="Upload recognized pieces to your own Nextcloud, to train a custom model later."
/>
<NextcloudSection />
