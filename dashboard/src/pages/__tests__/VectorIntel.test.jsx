import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import VectorIntel from '../VectorIntel'

vi.mock('../../services/api')

describe('<VectorIntel />', () => {
  it('renders the vector intelligence header', async () => {
    renderWithRouter(<VectorIntel />)
    expect(await screen.findByRole('heading', { name: /Vector Intelligence/i })).toBeInTheDocument()
  })
})
