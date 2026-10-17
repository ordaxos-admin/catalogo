import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { AppErrorBoundary } from './components/AppErrorBoundary'
import { CatalogPublicationBanner } from './components/CatalogPublicationBanner'
import { CollectionsDock } from './components/CollectionsDock'
import { ConnectivityBanner } from './components/ConnectivityBanner'
import { SharedCollectionsDock } from './components/SharedCollectionsDock'
import { installDialogAccessibility } from './installDialogAccessibility'
import { installLightboxSwipe } from './installLightboxSwipe'
import './styles.css'
import './hierarchy.css'
import './runtime.css'
import './premium.css'
import './lightbox.css'
import './resilience.css'
import './product-polish.css'
import './media-fallback.css'
import './discovery.css'
import './swipe-navigation.css'
import './explorer.css'
import './quote-confirmation.css'
import './collection-gallery.css'
import './gallery-navigation.css'
import './catalog-updates.css'
import './comparison.css'
import './collections.css'
import './collections-organizer.css'
import './collections-sharing.css'
import './publication-status.css'
import './catalog-shell.css'
import './catalog-home.css'
import './model-detail.css'
import './favorites.css'
import './quote-dialog.css'

function mediaContainer(image: HTMLImageElement) {
  return image.closest(
    '.model-art, .gallery-real-image, .image-lightbox__stage, .storefront-model-card__media, .model-detail-media, .comparison-cover',
  )
}

function installMediaFallback() {
  document.addEventListener('error', (event) => {
    const image = event.target
    if (!(image instanceof HTMLImageElement)) return
    image.dataset.mediaFailed = 'true'
    mediaContainer(image)?.classList.add('media-failed')
  }, true)

  document.addEventListener('load', (event) => {
    const image = event.target
    if (!(image instanceof HTMLImageElement)) return
    delete image.dataset.mediaFailed
    mediaContainer(image)?.classList.remove('media-failed')
  }, true)
}

installMediaFallback()
installLightboxSwipe()
installDialogAccessibility()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppErrorBoundary>
      <ConnectivityBanner />
      <CatalogPublicationBanner />
      <App />
      <CollectionsDock />
      <SharedCollectionsDock />
    </AppErrorBoundary>
  </StrictMode>,
)
