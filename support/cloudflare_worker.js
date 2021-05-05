/**
 * django-feed-reader cloudflare feed reader
 * This worker allows django-feed-reader crawlers to bypass
 * cloudflare's protection by fetching the feed on cloudflare's own
 * infrastrcture.
 * 
 * To use, create a new cloudflare worker and replace it with the
 * contents of this file. 
 * 
 * Then add an entry to your django settings (replacing the placeholders as appropriate)
 * 
 * FEEDS_CLOUDFLARE_WORKER='https://[name-of-worker].[your-worker-account].workers.dev'
 */

addEventListener('fetch', function(event) {
  const { request } = event
  const response = handleRequest(request).catch(handleError)
  event.respondWith(response)
})

/**
 * Receives a HTTP request and replies with a response.
 * @param {Request} request
 * @returns {Promise<Response>}
 */
async function handleRequest(request) {
  const { method, url } = request
  const { host, pathname } = new URL(url)

  switch (pathname) {
    case '/read/':
      return respondRead(request, url)
    case '/':
    case '/favicon.ico':
    case '/robots.txt':
      return new Response(null, { status: 204 })
  }

  // Workers on these hostnames have no origin server,
  // therefore there is nothing else to be found
  if (host.endsWith('.workers.dev')
      || host.endsWith('.cloudflareworkers.com')) {
    return new Response('Not Found', { status: 404 })
  }

  // Makes a fetch request to the origin server
  return fetch(request)
}

/**
 * Responds with an uncaught error.
 * @param {Error} error
 * @returns {Response}
 */
function handleError(error) {
  console.error('Uncaught error:', error)

  const { stack } = error
  return new Response(stack || error, {
    status: 500,
    headers: {
      'Content-Type': 'text/plain;charset=UTF-8'
    }
  })
}

async function respondRead(request, url) {

  const { searchParams } = new URL(url)

  let target = searchParams.get('target')

  return fetch(target)

}
